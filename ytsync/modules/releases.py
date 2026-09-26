import logging
import os
from collections.abc import AsyncGenerator

import httpx

from ytsync.version import __version__

LOGGER = logging.getLogger("ytsync")


class GitHub:
    """Provides asynchronous access to GitHub release information.

    >>> GitHub

    """

    BASE_URL = "https://api.github.com/repos/thevickypedia/YTSync"

    def __init__(self):
        """Initialize the GitHub API client."""
        git_token = (
            os.getenv("GIT_TOKEN")
            or os.getenv("git_token")
            or os.getenv("GITHUB_TOKEN")
            or os.getenv("github_token")
            or ""
        )
        headers = {
            "Accept": "application/vnd.github.v3+json",
        }
        if git_token:
            headers["Authorization"] = f"Bearer {git_token}"
        self.client = httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(None, connect=3, read=3),
        )

    async def get_git_releases(self) -> AsyncGenerator[str]:
        """Get GitHub release tag names asynchronously.

        Handles pagination so that large release lists can be processed.

        Yields:
            str:
            The tag name of each GitHub release.
        """
        # Handle pagination for large release lists
        page = 1
        while True:
            try:
                response = await self.client.get(
                    f"{self.BASE_URL}/releases?per_page=100&page={page}",
                )
                response.raise_for_status()
            except httpx.HTTPError:
                break

            releases = response.json()
            if not releases or not isinstance(releases, list):
                break
            for release in releases:
                yield release["tag_name"]

            page += 1

    async def get_release_sha(self, tag: str) -> str | None:
        """Get the SHA associated with a Git tag.

        Args:
            tag: The Git tag whose SHA should be retrieved.

        Returns:
            str | None:
            The tag's SHA, or None if the request fails or the response does not contain a SHA.
        """
        try:
            response = await self.client.get(
                f"{self.BASE_URL}/git/ref/tags/{tag}",
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return None

        if response_json := response.json():
            return (response_json.get("object", {}) or {}).get("sha")

    async def get_latest_sha(self) -> str | None:
        """Get the SHA of the latest commit on the default branch.

        Returns:
            str | None:
            The latest commit SHA, or None if the request fails or the response does not contain a SHA.
        """
        try:
            response = await self.client.get(
                f"{self.BASE_URL}/commits",
                params={"per_page": 1},  # only need the latest
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return None

        response_json = response.json()
        if isinstance(response_json, list):
            return response_json[0].get("sha")

    async def resolve_api_version(self) -> str:
        """Resolve the current API version from GitHub release information.

        Returns:
            str:
            The package version if it matches the latest default-branch commit,
            otherwise the package version followed by the first eight characters of the current commit SHA.
        """
        current_sha = await self.get_latest_sha()

        async for tag_name in self.get_git_releases():
            if tag_name.lstrip("v") == __version__:
                if current_sha == (await self.get_release_sha(tag_name) or ""):
                    return __version__

        else:
            return f"{__version__}:{current_sha[:8]}" if current_sha else f"{__version__}:dev"


github = GitHub()
