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

    OWNER = "thevickypedia"
    REPO = "YTSync"
    BASE_WEB_URL = f"https://github.com/{OWNER}/{REPO}"
    BASE_API_URL = f"https://api.github.com/repos/{OWNER}/{REPO}"

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
            timeout=httpx.Timeout(None, connect=3, read=5),
        )

    async def get_git_releases(self) -> AsyncGenerator[str]:
        """Get GitHub release tag names with pagination for large release lists.

        Yields:
            str:
            The tag name of each GitHub release.
        """
        # Handle pagination for large release lists
        page = 1
        while True:
            try:
                response = await self.client.get(
                    f"{self.BASE_API_URL}/releases?per_page=100&page={page}",
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
            The tag's SHA, or None if the request fails or the response does not contain a commit SHA.
        """
        try:
            response = await self.client.get(
                f"{self.BASE_API_URL}/git/ref/tags/{tag}",
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return None

        if response_json := response.json():
            return (response_json.get("object", {}) or {}).get("sha")
        return None

    async def get_latest_sha(self) -> str | None:
        """Get the SHA of the latest commit on the default branch.

        Returns:
            str | None:
            The latest commit SHA, or None if the request fails or the response does not contain a commit SHA.
        """
        try:
            response = await self.client.get(
                f"{self.BASE_API_URL}/commits",
                params={"per_page": 1},  # only need the latest
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return None

        response_json = response.json()
        if isinstance(response_json, list):
            if isinstance(response_json[0], dict):
                return response_json[0].get("sha")
        return None

    async def resolve_api_version(self) -> str:
        """Return the resolver result and close the async client."""
        try:
            return await self._resolve_api_version()
        finally:
            await self.client.aclose()

    async def _resolve_api_version(self) -> str:
        """Resolve the current API version from GitHub release information.

        Returns:
            str:
            The package version if it matches the latest default-branch commit,
            otherwise the package version followed by the first eight characters of the current commit SHA.
        """
        current_sha = await self.get_latest_sha()

        async for tag_name in self.get_git_releases():
            if tag_name.lstrip("v") == __version__:
                release_sha = await self.get_release_sha(tag_name)
                if current_sha and release_sha and current_sha == release_sha:
                    self.BASE_WEB_URL += f"/releases/tag/v{__version__}"
                    return __version__

        else:
            if current_sha:
                self.BASE_WEB_URL += f"/commit/{current_sha[:7]}"
                return f"{__version__}:{current_sha[:7]}"
            return f"{__version__}:dev"


github = GitHub()
