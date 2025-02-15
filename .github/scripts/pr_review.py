import os
import sys
from typing import List, Dict, Optional
import anthropic
import requests
import base64
import json
import logging

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

class PRReviewer:
    def __init__(self):
        self.github_token = os.environ["GITHUB_TOKEN"]
        self.anthropic_key = os.environ["ANTHROPIC_API_KEY"]
        self.event_path = os.environ["GITHUB_EVENT_PATH"]
        self.repository = os.environ["GITHUB_REPOSITORY"]
        self.base_url = "https://api.github.com"
        
        # Initialize API clients
        self.claude = anthropic.Client(api_key=self.anthropic_key)
        self.headers = {
            "Authorization": f"token {self.github_token}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        # Load PR event data
        try:
            with open(self.event_path, 'r') as f:
                self.event_data = json.load(f)
            self.pr_number = self.event_data["number"]
            logger.info(f"Initialized PR reviewer for PR #{self.pr_number}")
        except Exception as e:
            logger.error(f"Error loading event data: {e}")
            logger.debug(f"Event path: {self.event_path}")
            logger.debug(f"Current directory: {os.getcwd()}")
            logger.debug(f"Directory contents: {os.listdir('.')}")
            raise
    
    def get_changed_files(self) -> List[Dict]:
        """Fetch list of changed files in the PR."""
        url = f"{self.base_url}/repos/{self.repository}/pulls/{self.pr_number}/files"
        logger.debug(f"Fetching changed files from: {url}")
        response = requests.get(url, headers=self.headers)
        response.raise_for_status()
        files = response.json()
        logger.info(f"Found {len(files)} changed files")
        return files
    
    def get_file_content(self, file_path: str, sha: str) -> Optional[str]:
        """Fetch content of a specific file."""
        url = f"{self.base_url}/repos/{self.repository}/git/blobs/{sha}"
        logger.debug(f"Fetching file content from: {url}")
        response = requests.get(url, headers=self.headers)
        response.raise_for_status()
        
        content = response.json()["content"]
        encoding = response.json()["encoding"]
        logger.debug(f"File encoding: {encoding}")
        
        if encoding == "base64":
            return base64.b64decode(content).decode('utf-8')
        return content
    
    def create_review_comment(self, file_path: str, line_number: int, body: str, commit_id: str):
        """Create a review comment on a specific line."""
        url = f"{self.base_url}/repos/{self.repository}/pulls/{self.pr_number}/comments"
        data = {
            "body": body,
            "path": file_path,
            "line": line_number,
            "commit_id": commit_id
        }
        logger.debug(f"Creating review comment at {file_path}:{line_number}")
        response = requests.post(url, headers=self.headers, json=data)
        response.raise_for_status()
        logger.info(f"Created review comment for {file_path}:{line_number}")
    
    def review_code(self, code: str, file_path: str) -> List[Dict]:
        """Send code to Claude API for review."""
        logger.info(f"Starting code review for: {file_path}")
        
        prompt = f"""Please review the following code and provide specific, actionable feedback about:
1. Potential bugs or errors
2. Code quality improvements
3. Performance optimizations
4. Security concerns

For each issue, provide:
- The line number
- A clear explanation of the issue
- A suggested fix or improvement

The code is from the file: {file_path}

Code to review:
```
{code}
```

Respond ONLY with a JSON array of objects, each with exactly these fields, no other text:
[
    {
        "line": <line_number>,
        "comment": "Your detailed comment here"
    }
]"""

        try:
            logger.debug("Sending request to Claude API")
            response = self.claude.messages.create(
                model="claude-3-opus-20240229",
                max_tokens=2000,
                temperature=0,
                system="You are a senior software engineer performing a code review. Be thorough but constructive. Focus on important issues rather than style nitpicks.",
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            
            logger.debug(f"Claude API response: {response.content[0].text}")
            
            # Extract JSON array from Claude's response
            review_comments = json.loads(response.content[0].text)
            logger.info(f"Parsed {len(review_comments)} review comments")
            return review_comments
            
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing Claude's response: {e}")
            logger.debug(f"Raw response: {response.content[0].text}")
            return []
        except Exception as e:
            logger.error(f"Error during code review: {e}")
            return []

    def run_review(self):
        """Main method to run the PR review process."""
        try:
            changed_files = self.get_changed_files()
            
            for file in changed_files:
                # Skip binary files and deletions
                if file["status"] == "removed" or file.get("binary", False):
                    logger.info(f"Skipping {file['filename']} (status: {file['status']}, binary: {file.get('binary', False)})")
                    continue
                    
                file_path = file["filename"]
                logger.info(f"Reviewing: {file_path}")
                
                # Get file content
                content = self.get_file_content(file["filename"], file["sha"])
                if not content:
                    logger.warning(f"No content found for {file_path}")
                    continue
                
                # Get review comments from Claude
                review_comments = self.review_code(content, file_path)
                
                # Post comments
                for comment in review_comments:
                    try:
                        self.create_review_comment(
                            file_path=file_path,
                            line_number=comment["line"],
                            body=comment["comment"],
                            commit_id=file["sha"]
                        )
                    except Exception as e:
                        logger.error(f"Error posting comment: {e}")
                        logger.debug(f"Comment data: {comment}")

        except Exception as e:
            logger.error(f"Error in run_review: {e}")
            raise

def main():
    try:
        logger.info("Starting PR review")
        reviewer = PRReviewer()
        reviewer.run_review()
        logger.info("PR review completed successfully")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
