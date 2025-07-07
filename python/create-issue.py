"""
When you have created a webhook for a specific repository, you can use this script to create an issue in the repo.

This is an initial baseline just to show-case how webhooks work.
"""

import asyncio
import os
import sys

import aiohttp
from gidgethub.aiohttp import GitHubAPI

REPOSITORY = "Lightning-Sandbox/demo-with-Bots"


async def main(repo: str = REPOSITORY):
    async with aiohttp.ClientSession() as session:
        gh = GitHubAPI(session, "your-bot-name", oauth_token=os.getenv("GH_AUTH"))

        # Confirm access
        await gh.getitem(f"/repos/{repo}")

        # Create a new issue
        issue = await gh.post(
            f"/repos/{repo}/issues",
            data={"title": "🤖 Bot-created issue", "body": "Testing issue creation in an org repo!"},
        )
        print("Created issue:", issue["html_url"])


if __name__ == "__main__":
    # Ensure the GH_AUTH environment variable is set
    if "GH_AUTH" not in os.environ:
        raise OSError("Please set the GH_AUTH environment variable with your GitHub token.")

    # Run the main function
    repo = sys.argv[1] if len(sys.argv) > 1 else REPOSITORY
    print("Creating issue in", repo)
    asyncio.run(main(repo))
