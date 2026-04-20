from __future__ import annotations

from functools import lru_cache

import httpx

from app.config import get_settings


class MattermostClient:
    """Minimal Mattermost REST client using a Bot access token (sync)."""

    def __init__(self, base_url: str | None = None, token: str | None = None):
        s = get_settings()
        self._base_url = (base_url or s.mattermost_url).rstrip("/")
        self._token = token or s.mattermost_bot_token
        self._client = httpx.Client(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=10.0,
        )
        self._bot_user_id: str | None = None

    def close(self) -> None:
        self._client.close()

    # --- Users ---------------------------------------------------------
    def me(self) -> dict:
        r = self._client.get("/api/v4/users/me")
        r.raise_for_status()
        return r.json()

    @property
    def bot_user_id(self) -> str:
        if not self._bot_user_id:
            self._bot_user_id = self.me()["id"]
        return self._bot_user_id

    def get_user_by_username(self, username: str) -> dict | None:
        r = self._client.get(f"/api/v4/users/username/{username}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def get_user(self, user_id: str) -> dict:
        r = self._client.get(f"/api/v4/users/{user_id}")
        r.raise_for_status()
        return r.json()

    def list_active_users(self, per_page: int = 200) -> list[dict]:
        """Fetch all active, non-bot, non-deleted users (paginated)."""
        out: list[dict] = []
        page = 0
        while True:
            r = self._client.get(
                "/api/v4/users",
                params={"page": page, "per_page": per_page, "active": "true"},
            )
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            for u in batch:
                if u.get("delete_at", 0) > 0:
                    continue
                if u.get("is_bot") or u.get("roles", "").find("system_bot") != -1:
                    continue
                out.append(u)
            if len(batch) < per_page:
                break
            page += 1
        return out

    # --- Channels / Posts ---------------------------------------------
    def create_direct_channel(self, user_a: str, user_b: str) -> dict:
        r = self._client.post("/api/v4/channels/direct", json=[user_a, user_b])
        r.raise_for_status()
        return r.json()

    def create_post(self, channel_id: str, message: str, props: dict | None = None) -> dict:
        payload: dict = {"channel_id": channel_id, "message": message}
        if props:
            payload["props"] = props
        r = self._client.post("/api/v4/posts", json=payload)
        r.raise_for_status()
        return r.json()

    def create_ephemeral_post(
        self,
        target_user_id: str,
        channel_id: str,
        message: str,
        props: dict | None = None,
    ) -> dict:
        post: dict = {"channel_id": channel_id, "message": message}
        if props:
            post["props"] = props
        r = self._client.post(
            "/api/v4/posts/ephemeral",
            json={"user_id": target_user_id, "post": post},
        )
        r.raise_for_status()
        return r.json()

    def update_post(
        self, post_id: str, message: str, props: dict | None = None
    ) -> dict:
        payload: dict = {"id": post_id, "message": message}
        if props is not None:
            payload["props"] = props
        r = self._client.put(f"/api/v4/posts/{post_id}", json=payload)
        r.raise_for_status()
        return r.json()

    def send_dm(self, to_user_id: str, message: str, props: dict | None = None) -> dict:
        ch = self.create_direct_channel(self.bot_user_id, to_user_id)
        return self.create_post(ch["id"], message, props=props)

    # --- Interactive ---------------------------------------------------
    def open_dialog(self, trigger_id: str, dialog_url: str, dialog: dict) -> None:
        r = self._client.post(
            "/api/v4/actions/dialogs/open",
            json={"trigger_id": trigger_id, "url": dialog_url, "dialog": dialog},
        )
        r.raise_for_status()


@lru_cache
def get_mattermost() -> MattermostClient:
    return MattermostClient()
