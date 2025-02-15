import os
import sys
from typing import List, Dict, Optional
import anthropic
from github import Github
import base64
import json
import logging
import re

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

class PRReviewer:
    def __init__(self):
        self.github_token = os.environ["GITHUB_TOKEN"]
        self.anthropic_key = os.environ["ANTHROPIC_API_KEY"]
        self.event_path = os.environ["GITHUB_EVENT_PATH"]
        self.repository = os.environ["GITHUB_REPOSITORY"]
        
        # Initialize API clients
        self.claude = anthropic.Client(api_key=self.anthropic_key)
        self.github = Github(self.github_token)
        
        # Load PR event data
        try:
            with open(self.event_path, 'r') as f:
                self.event_data = json.load(f)
            self.pr_number = self.event_data["number"]
            logger.info(f"Initialized PR reviewer for PR #{self.pr_number}")
            
            # Get repository and PR objects
            self.repo = self.github.get_repo(self.repository)
            self.pull_request = self.repo.get_pull(self.pr_number)
            
        except Exception as e:
            logger.error(f"Error initializing: {e}")
            raise

    def get_existing_comments(self):
        """Get all existing review comments on the PR."""
        comments = self.pull_request.get_review_comments()
        existing = {}
        for comment in comments:
            key = f"{comment.path}:{comment.position}"
            existing[key] = comment.body
        logger.debug(f"Found {len(existing)} existing comments: {existing}")
        return existing

    def calculate_line_positions(self, patch: str) -> Dict[int, int]:
        """Calculate the position of each line in the patch."""
        positions = {}
        lines = patch.split('\n')
        position = 0
        
        logger.debug(f"Processing patch:\n{patch}")
        
        for line in lines:
            if line.startswith('@@'):
                match = re.search(r'\@\@ \-\d+,?\d* \+(\d+),?(\d*)', line)
                if match:
                    current_line = int(match.group(1))
                    logger.debug(f"Found hunk starting at line {current_line}")
            else:
                position += 1
                if not line.startswith('-'):
                    positions[current_line] = position
                    current_line += 1
                    
        logger.debug(f"Line to position mapping: {json.dumps(positions, indent=2)}")
        return positions
    
    def review_code(self, code: str, file_path: str) -> List[Dict]:
        """Send code to Claude API for review."""
        logger.info(f"Starting code review for: {file_path}")
        
        prompt = f"""Review this code and respond with ONLY a JSON array of found issues. For each issue include:
- line number
- explanation of the issue
- concrete code suggestion for improvement

Format EXACTLY like this JSON array, with no other text:

[
    {{
        "line": 1,
        "comment": "Description of the issue and why it should be improved",
        "suggestion": "The exact code that should replace this line"
    }}
]

If no issues are found, respond with an empty array: []

The code to review is from {file_path}:

```
{code}
```"""

        try:
            logger.debug("Sending request to Claude API")
            response = self.claude.messages.create(
                model="claude-3-opus-20240229",
                max_tokens=2000,
                temperature=0,
                system="You are a senior software engineer performing a code review. Be thorough but constructive. Focus on important issues rather than style nitpicks. Always respond with properly formatted JSON.",
                messages=[{"role": "user", "content": prompt}]
            )
            
            logger.debug(f"Claude API raw response: {response.content[0].text}")
            
            try:
                review_comments = json.loads(response.content[0].text)
                if not isinstance(review_comments, list):
                    logger.error("Claude's response is not a JSON array")
                    return []
                    
                logger.info(f"Successfully parsed {len(review_comments)} review comments")
                return review_comments
                
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse Claude's response as JSON: {e}")
                return []
                
        except Exception as e:
            logger.error(f"Error during code review: {e}")
            return []

    def run_review(self):
        """Main method to run the PR review process."""
        try:
            changed_files = self.pull_request.get_files()
            draft_review_comments = []
            general_comments = []
            
            # Get existing comments to avoid duplicates
            existing_comments = self.get_existing_comments()
            
            for file in changed_files:
                if file.status == "removed":
                    logger.info(f"Skipping removed file: {file.filename}")
                    continue
                
                logger.info(f"Reviewing: {file.filename}")
                
                # Get file content
                try:
                    content = self.repo.get_contents(file.filename, ref=self.pull_request.head.sha).decoded_content.decode('utf-8')
                except Exception as e:
                    logger.error(f"Error getting file content: {e}")
                    continue
                
                # Calculate line positions in the patch
                if file.patch:
                    line_positions = self.calculate_line_positions(file.patch)
                    logger.debug(f"Line positions map: {line_positions}")
                else:
                    logger.warning(f"No patch found for {file.filename}")
                    continue
                
                # Get review comments from Claude
                file_comments = self.review_code(content, file.filename)
                
                # Convert comments to GitHub review format
                for comment in file_comments:
                    line_num = comment['line']
                    available_lines = sorted(line_positions.keys())
                    
                    # Find the closest available line in the patch
                    closest_idx = min(range(len(available_lines)), 
                                   key=lambda i: abs(available_lines[i] - line_num))
                    closest_line = available_lines[closest_idx]
                    
                    # Check if we're within a reasonable range
                    if abs(closest_line - line_num) <= 3:  # GitHub's typical context size
                        position = line_positions[closest_line]
                        logger.debug(f"Mapping comment from line {line_num} to position {position} (line {closest_line} in patch)")
                        
                        comment_body = f"{comment['comment']}\n\n```suggestion\n{comment.get('suggestion', '')}\n```"
                        comment_key = f"{file.filename}:{position}"
                        
                        # Check if we already have a similar comment
                        if comment_key not in existing_comments:
                            draft_review_comments.append({
                                'path': file.filename,
                                'position': position,
                                'body': comment_body
                            })
                    else:
                        logger.warning(f"Line {line_num} not found in patch context (closest was {closest_line})")
                        comment_body = f"**In file {file.filename}, line {line_num}:**\n\n{comment['comment']}\n\n```suggestion\n{comment.get('suggestion', '')}\n```"
                        general_comments.append(comment_body)
            
            if draft_review_comments or general_comments:
                logger.info(f"Creating review with {len(draft_review_comments)} inline comments and {len(general_comments)} general comments")
                
                review_body = "🤖 Code Review Summary:\n\n"
                if draft_review_comments:
                    review_body += f"Found {len(draft_review_comments)} suggestions for improvement."
                else:
                    review_body += "✨ Great job! The code looks clean and well-written."
                
                if general_comments:
                    review_body += "\n\n### Additional Comments:\n\n" + "\n\n".join(general_comments)
                
                commit = self.repo.get_commit(self.pull_request.head.sha)
                self.pull_request.create_review(
                    commit=commit,
                    comments=draft_review_comments,
                    body=review_body,
                    event="COMMENT"
                )
                logger.info("Review created successfully")

        except Exception as e:
            logger.error(f"Error in run_review: {e}", exc_info=True)
            raise

def main():
    try:
        logger.info("Starting PR review")
        reviewer = PRReviewer()
        reviewer.run_review()
        logger.info("PR review completed successfully")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
