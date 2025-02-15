import os
import sys
from typing import List, Dict, Optional
import anthropic
import requests
import base64
import json

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
        with open(self.event_path, 'r') as f:
            self.event_data = json.load(f)
            
        self.pr_number = self.event_data["number"]
        
    def get_changed_files(self) -> List[Dict]:
        """Fetch list of changed files in the PR."""
        url = f"{self.base_url}/repos/{self.repository}/pulls/{self.pr_number}/files"
        response = requests.get(url, headers=self.headers)
        response.raise_for_status()
        return response.json()
    
    def get_file_content(self, file_path: str, sha: str) -> Optional[str]:
        """Fetch content of a specific file."""
        url = f"{self.base_url}/repos/{self.repository}/git/blobs/{sha}"
        response = requests.get(url, headers=self.headers)
        response.raise_for_status()
        
        content = response.json()["content"]
        if response.json()["encoding"] == "base64":
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
        response = requests.post(url, headers=self.headers, json=data)
        response.raise_for_status()
    
    def review_code(self, code: str, file_path: str) -> List[Dict]:
        """Send code to Claude API for review."""
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

Format your response as a list of JSON objects, one per comment, with the following structure:
{{
    "line": <line_number>,
    "comment": "Your detailed comment here"
}}"""

        response = self.claude.messages.create(
            model="claude-3-opus-20240229",
            max_tokens=2000,
            temperature=0,
            system="You are a senior software engineer performing a code review. Be thorough but constructive. Focus on important issues rather than style nitpicks.",
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        
        try:
            # Extract JSON array from Claude's response
            review_comments = json.loads(response.content[0].text)
            return review_comments
        except (json.JSONDecodeError, IndexError):
            print(f"Error parsing Claude's response for {file_path}")
            return []

    def run_review(self):
        """Main method to run the PR review process."""
        changed_files = self.get_changed_files()
        
        for file in changed_files:
            # Skip binary files and deletions
            if file["status"] == "removed" or file.get("binary", False):
                continue
                
            file_path = file["filename"]
            print(f"Reviewing: {file_path}")
            
            # Get file content
            content = self.get_file_content(file["filename"], file["sha"])
            if not content:
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
                    print(f"Error posting comment: {e}")

def main():
    try:
        reviewer = PRReviewer()
        reviewer.run_review()
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()

