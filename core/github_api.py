import os
import logging
import base64
import aiohttp
import asyncio
from typing import Optional, Dict, Any, Tuple
import datetime

logger = logging.getLogger(__name__)

class GitHubAPI:
    def __init__(self, token: Optional[str] = None, repo: Optional[str] = None):
        self.token = token or os.getenv("GITHUB_TOKEN")
        self.username = os.getenv("GITHUB_USERNAME", "LakshyaBot")  # Use env var or default to LakshyaBot
        
        # If repo is provided, save it; otherwise use hardcoded value
        if repo:
            # If a full repo string with username/repo format is provided, extract just the repo name
            if "/" in repo:
                self.repo = repo
            else:
                self.repo = f"{self.username}/{repo}"
        else:
            self.repo = f"{self.username}/tickets"  # Hardcoded repository as fallback
            
        self.api_url = "https://api.github.com"
        
        if not self.token:
            logger.warning("GitHub token not provided or found in environment")
    
    def set_repo(self, repo: str):
        """
        Set the repository name, but only if it's not already set or if different
        """
        if repo and repo != self.repo:
            # If a full repo string with username/repo format is provided, use as is
            if "/" in repo:
                self.repo = repo
            else:
                self.repo = f"{self.username}/{repo}"
            logger.info(f"Repository set to {self.repo}")
    
    async def create_file(self, path: str, content: str, commit_message: str) -> Tuple[bool, Optional[str]]:
        """
        Create a file in the GitHub repository
        Returns (success, html_url or error message)
        """
        if not self.token or not self.repo:
            return False, "GitHub token or repository not configured"
        
        headers = {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        encoded_content = base64.b64encode(content.encode()).decode()
        
        data = {
            "message": commit_message,
            "content": encoded_content
        }
        
        url = f"{self.api_url}/repos/{self.repo}/contents/{path}"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.put(url, headers=headers, json=data) as response:
                    response_data = await response.json()
                    
                    if response.status == 201:
                        # Get the html_url from the response
                        html_url = self._get_github_pages_url(path)
                        return True, html_url
                    else:
                        error_msg = response_data.get("message", "Unknown error")
                        logger.error(f"Failed to create file: {error_msg}")
                        return False, f"GitHub API error: {error_msg}"
        except Exception as e:
            logger.error(f"Error in GitHub API request: {e}")
            return False, f"Error: {str(e)}"
    
    def _get_github_pages_url(self, path: str) -> str:
        """
        Convert GitHub repo path to GitHub Pages URL
        For example: "tickets/server123/ticket456.html" ->
        "https://username.github.io/repo-name/tickets/server123/ticket456.html"
        """
        repo_name = self.repo.split('/')[-1] if self.repo else ""
        return f"https://{self.username}.github.io/{repo_name}/{path}"
        
    async def check_repo_exists(self) -> bool:
        """Check if the configured repository exists"""
        if not self.token or not self.repo:
            return False
        
        headers = {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        url = f"{self.api_url}/repos/{self.repo}"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    return response.status == 200
        except Exception as e:
            logger.error(f"Error checking repository: {e}")
            return False
    
    async def check_pages_enabled(self) -> bool:
        """Check if GitHub Pages is enabled for the repository"""
        if not self.token or not self.repo:
            return False
        
        headers = {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        url = f"{self.api_url}/repos/{self.repo}/pages"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    return response.status == 200
        except Exception as e:
            logger.error(f"Error checking GitHub Pages: {e}")
            return False
