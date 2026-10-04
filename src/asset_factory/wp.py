"""WordPress transport.

Two important facts about this particular stack, both discovered by probing
the live clone rather than assumed:

1. Rank Math's fields are NOT registered for the standard REST meta API.
   `GET /wp/v2/pages/<id>` returns meta == {"footnotes": ...} and nothing
   else. Sending {"meta": {"rank_math_title": "..."}} to /wp/v2/pages/<id>
   returns HTTP 200 and writes nothing -- a silent no-op that looks like
   success in every log. Rank Math's own namespace (/rankmath/v1/updateMeta)
   is the real write path.

2. Reads for the audit deliberately use the RENDERED page, not the database.
   What a search engine and a visitor actually see is the ground truth for
   titles, meta descriptions, phone links and CTAs; a stored value that the
   theme never outputs is not a passing result.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

import requests

DEFAULT_TIMEOUT = 30


class WPError(RuntimeError):
    pass


@dataclass
class WPClient:
    base_url: str
    user: str | None = None
    app_password: str | None = None
    timeout: int = DEFAULT_TIMEOUT
    _session: requests.Session = field(default_factory=requests.Session, repr=False)

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/") + "/"
        if self.user and self.app_password:
            # Application Password, never the account login password.
            self._session.auth = (self.user, self.app_password.replace(" ", ""))

    @property
    def authenticated(self) -> bool:
        return self._session.auth is not None

    # -- plumbing ---------------------------------------------------------
    def _url(self, path: str) -> str:
        return urljoin(self.base_url, path.lstrip("/"))

    def _request(self, method: str, path: str, **kw) -> Any:
        resp = self._session.request(
            method, self._url(path), timeout=self.timeout, **kw
        )
        if resp.status_code >= 400:
            raise WPError(
                f"{method} {path} -> HTTP {resp.status_code}: {resp.text[:300]}"
            )
        if not resp.content:
            return None
        try:
            return resp.json()
        except json.JSONDecodeError:
            return resp.text

    # -- reads (no auth required for published content) -------------------
    def site_info(self) -> dict:
        return self._request("GET", "wp-json/")

    def pages(self) -> list[dict]:
        return self._request(
            "GET",
            "wp-json/wp/v2/pages",
            params={"per_page": 100, "status": "publish,draft,private"}
            if self.authenticated
            else {"per_page": 100},
        )

    def page(self, page_id: int) -> dict:
        return self._request("GET", f"wp-json/wp/v2/pages/{page_id}")

    def rendered(self, url: str) -> str:
        resp = self._session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        return resp.text

    def seo_meta(self, page_id: int) -> dict:
        """Rank Math values as stored. Requires auth; empty dict if refused."""
        if not self.authenticated:
            return {}
        try:
            data = self._request(
                "GET", f"wp-json/wp/v2/pages/{page_id}", params={"context": "edit"}
            )
        except WPError:
            return {}
        return {k: v for k, v in (data.get("meta") or {}).items() if "rank_math" in k}

    # -- writes (auth required) -------------------------------------------
    def _require_auth(self) -> None:
        if not self.authenticated:
            raise WPError(
                "write attempted without credentials; set WP_USER and "
                "WP_APP_PASSWORD in .env"
            )

    def update_page_fields(self, page_id: int, **fields) -> dict:
        """Update native page fields (title, content, slug, status)."""
        self._require_auth()
        return self._request("POST", f"wp-json/wp/v2/pages/{page_id}", json=fields)

    def update_rank_math(self, page_id: int, **meta) -> dict:
        """Write Rank Math fields through Rank Math's own REST namespace.

        The standard meta API is not an option here -- see the module note.
        Keys are e.g. rank_math_title, rank_math_description.
        """
        self._require_auth()
        payload = {
            "objectID": page_id,
            "objectType": "post",
            "meta": {k: v for k, v in meta.items() if v is not None},
        }
        result = self._request("POST", "wp-json/rankmath/v1/updateMeta", json=payload)
        return result if isinstance(result, dict) else {"result": result}

    def settings(self) -> dict:
        self._require_auth()
        return self._request("GET", "wp-json/wp/v2/settings")

    def update_settings(self, **fields) -> dict:
        """Site title / tagline live here (blogname, blogdescription)."""
        self._require_auth()
        return self._request("POST", "wp-json/wp/v2/settings", json=fields)

    def menus(self) -> list[dict]:
        self._require_auth()
        return self._request("GET", "wp-json/wp/v2/menus",
                             params={"per_page": 100})

    def menu_items(self, menu_id: int) -> list[dict]:
        self._require_auth()
        return self._request(
            "GET", "wp-json/wp/v2/menu-items", params={"menus": menu_id, "per_page": 100}
        )

    def update_menu_item(self, item_id: int, **fields) -> dict:
        self._require_auth()
        return self._request(
            "POST", f"wp-json/wp/v2/menu-items/{item_id}", json=fields
        )

    def verify_write_path(self) -> dict[str, bool]:
        """Pre-flight: confirm each write channel actually works.

        Guards against the silent-no-op class of failure by checking that the
        endpoints exist and respond, before a run reports success.
        """
        report: dict[str, bool] = {}
        root = self.site_info()
        namespaces = root.get("namespaces", [])
        report["wp/v2"] = "wp/v2" in namespaces
        report["rankmath/v1"] = "rankmath/v1" in namespaces
        report["authenticated"] = self.authenticated
        if self.authenticated:
            try:
                self.settings()
                report["settings_writable"] = True
            except WPError:
                report["settings_writable"] = False
        return report
