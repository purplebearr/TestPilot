"""
LocalChromiumBrowser subclass that stores screenshots in a directory scoped
to the browser instance, instead of the shared STRANDS_BROWSER_SCREENSHOTS_DIR
environment variable.

Why: the base implementation reads a single process-wide env var on every
screenshot call. That's fine for one agent, but breaks down the moment you
run several agents (threads, async tasks, or just multiple instances) in the
same process, since they'd all fight over the same global directory.

This subclass keeps the exact same behavior (auto-generated filename when no
path is given, relative paths joined under the directory, absolute paths used
as-is) but resolves against `self._screenshots_dir` instead of os.environ.
"""

import os
import time
from typing import Any, Dict

from strands_tools.browser import LocalChromiumBrowser
from strands_tools.browser.models import ScreenshotAction


class ScopedLocalChromiumBrowser(LocalChromiumBrowser):
    """LocalChromiumBrowser that writes screenshots to a fixed, per-instance directory."""

    def __init__(self, screenshots_dir: str, **kwargs: Any):
        """
        Args:
            screenshots_dir: Directory this browser instance's screenshots are
                written to. Created if it doesn't already exist.
            **kwargs: Passed through to LocalChromiumBrowser (launch_options,
                context_options).
        """
        super().__init__(**kwargs)
        self._screenshots_dir = screenshots_dir
        os.makedirs(self._screenshots_dir, exist_ok=True)

    def screenshot(self, action: ScreenshotAction) -> Dict[str, Any]:
        """Take a screenshot, saved under this instance's screenshots directory."""
        return self._execute_async(self._async_screenshot(action))

    async def _async_screenshot(self, action: ScreenshotAction) -> Dict[str, Any]:
        error_response = self.validate_session(action.session_name)
        if error_response:
            return error_response

        page = self.get_session_page(action.session_name)
        if not page:
            return {"status": "error", "content": [{"text": "Error: No active page for session"}]}

        try:
            os.makedirs(self._screenshots_dir, exist_ok=True)

            if not action.path:
                filename = f"screenshot_{int(time.time())}.png"
                path = os.path.join(self._screenshots_dir, filename)
            elif not os.path.isabs(action.path):
                path = os.path.join(self._screenshots_dir, action.path)
            else:
                # Agent explicitly gave an absolute path - respect it.
                path = action.path

            await page.screenshot(path=path)
            return {"status": "success", "content": [{"text": f"Screenshot saved as {path}"}]}
        except Exception as e:
            return {"status": "error", "content": [{"text": f"Error: {str(e)}"}]}