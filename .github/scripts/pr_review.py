import os
import sys
from typing import List, Dict, Optional
import anthropic
from github import Github
import base64
import json
import logging
import re
from fnmatch import fnmatch
from dataclasses import dataclass

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

@dataclass
class FileFilterConfig:
    whitelist_patterns: List[str]
    blacklist_patterns: List[str]

    @classmethod
    def from_env(cls) -> 'FileFilterConfig':
        """Create config from environment variables."""
        whitelist = os.getenv('PR_REVIEW_WHITELIST', '').split(',')
        blacklist = os.getenv('PR_REVIEW_BLACKLIST', '').split(',')
        
        # Clean up empty strings and whitespace
        whitelist = [p.strip() for p in whitelist if p.strip()]
        blacklist = [p.strip() for p in blacklist if p.strip()]
        
        # If no whitelist is specified, default to allowing all files
        if not whitelist:
            whitelist = ['*']
            
        return cls(whitelist_patterns=whitelist, blacklist_patterns=blacklist)
    
    def should_review_file(self, filename: str) -> bool:
        """
        Determine if a file should be reviewed based on whitelist and blacklist patterns.
        Blacklist takes precedence over whitelist.
        """
        # First check blacklist - if file matches any blacklist pattern, exclude it
        for pattern in self.blacklist_patterns:
            if fnmatch(filename, pattern):
                logger.debug(f"File {filename} matched blacklist pattern {pattern}")
                return False
                
        # Then check whitelist - file must match at least one whitelist pattern
        for pattern in self.whitelist_patterns:
            if fnmatch(filename, pattern):
                logger.debug(f"File {filename} matched whitelist pattern {pattern}")
                return True
                
        logger.debug(f"File {filename} did not match any whitelist patterns")
        return False

class PRReviewer:
    def __init__(self):
        self.github_token = os.environ["GITHUB_TOKEN"]
        self.anthropic_key = os.environ["ANTHROPIC_API_KEY"]
        self.event_path = os.environ["GITHUB_EVENT_PATH"]
        self.repository = os.environ["GITHUB_REPOSITORY"]
        
        # Initialize file filter config
        self.file_filter = FileFilterConfig.from_env()
        logger.info(f"Initialized with whitelist: {self.file_filter.whitelist_patterns}")
        logger.info(f"Initialized with blacklist: {self.file_filter.blacklist_patterns}")
        
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
        """
        Calculate the position of each line in the patch with improved accuracy.
        Returns a mapping of actual file line numbers to patch positions.
        """
        positions = {}
        lines = patch.split('\n')
        position = 0
        current_line = 0
        in_hunk = False
        
        logger.debug(f"Processing patch:\n{patch}")
        
        for line in lines:
            # Parse hunk header
            if line.startswith('@@'):
                in_hunk = True
                match = re.search(r'\@\@ \-\d+,?\d* \+(\d+),?(\d*)', line)
                if match:
                    current_line = int(match.group(1))
                    logger.debug(f"Found hunk starting at line {current_line}")
                    position += 1
                    continue
            
            if not in_hunk:
                continue
                
            # Track position for every line in the patch
            position += 1
            
            # Only map lines that are context or additions (not removals)
            if not line.startswith('-'):
                if line.startswith('+'):
                    positions[current_line] = position
                else:  # Context line
                    positions[current_line] = position
                current_line += 1
        
        logger.debug(f"Line to position mapping: {json.dumps(positions, indent=2)}")
        return positions
    
    def find_closest_line(self, target_line: int, positions: Dict[int, int], 
                         max_distance: int = 3) -> Optional[int]:
        """
        Find the closest available line in the patch within max_distance.
        Returns actual line number if found, None if no suitable line is found.
        """
        if target_line in positions:
            return target_line
            
        available_lines = sorted(positions.keys())
        if not available_lines:
            return None
            
        # Find closest line that's within max_distance
        closest_line = min(available_lines, 
                          key=lambda x: abs(x - target_line))
                          
        if abs(closest_line - target_line) <= max_distance:
            return closest_line
        return None
    
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
                model="claude-3-7-sonnet-20250219",
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
            
            skipped_files = []
            reviewed_files = []
            
            for file in changed_files:
                if file.status == "removed":
                    logger.info(f"Skipping removed file: {file.filename}")
                    continue
                
                # Check if file should be reviewed based on filters
                if not self.file_filter.should_review_file(file.filename):
                    logger.info(f"Skipping {file.filename} based on filter configuration")
                    skipped_files.append(file.filename)
                    continue
                    
                reviewed_files.append(file.filename)
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
                            
                            if file.patch:
                                line_positions = self.calculate_line_positions(file.patch)
                                logger.debug(f"Line positions map: {line_positions}")
                                
                                # Find appropriate line to attach comment to
                                mapped_line = self.find_closest_line(line_num, line_positions)
                                
                                if mapped_line is not None:
                                    position = line_positions[mapped_line]
                                    logger.debug(f"Mapping comment from line {line_num} to position {position} (line {mapped_line} in patch)")
                                    
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
                                    logger.warning(f"Line {line_num} not found in patch context")
                                    comment_body = f"**In file {file.filename}, line {line_num}:**\n\n{comment['comment']}\n\n```suggestion\n{comment.get('suggestion', '')}\n```"
                                    general_comments.append(comment_body)
                            else:
                                logger.warning(f"No patch found for {file.filename}")
                                continue
            
            if draft_review_comments or general_comments or skipped_files:
                logger.info(f"Creating review with {len(draft_review_comments)} inline comments and {len(general_comments)} general comments")
                
                review_body = "🤖 Code Review Summary:\n\n"
                
                if reviewed_files:
                    review_body += f"Reviewed {len(reviewed_files)} files:\n"
                    for filename in reviewed_files:
                        review_body += f"- {filename}\n"
                
                if skipped_files:
                    review_body += f"\nSkipped {len(skipped_files)} files based on filter configuration:\n"
                    for filename in skipped_files:
                        review_body += f"- {filename}\n"
                
                if draft_review_comments:
                    review_body += f"\nFound {len(draft_review_comments)} suggestions for improvement."
                else:
                    review_body += "\n✨ Great job! The code looks clean and well-written."
                
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
            else:
                logger.info("No files were reviewed or no comments to make")

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
