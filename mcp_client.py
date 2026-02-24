from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests


@dataclass
class MCPServerConfig:
    name: str
    base_url: str
    auth_type: str = "bearer"
    auth_token: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    timeout: int = 20

    def masked(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "base_url": self.base_url,
            "auth_type": self.auth_type,
            "has_token": bool(self.auth_token),
            "headers": {k: "***" for k in (self.headers or {}).keys()},
            "timeout": self.timeout,
        }


class MCPClient:
    def __init__(self, config: MCPServerConfig):
        self.config = config
        self._session = requests.Session()

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.headers:
            headers.update(self.config.headers)
        if self.config.auth_token:
            if self.config.auth_type == "bearer":
                headers["Authorization"] = f"Bearer {self.config.auth_token}"
            elif self.config.auth_type == "oauth":
                headers["Authorization"] = f"Bearer {self.config.auth_token}"
        return headers

    def _rpc(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": method,
            "params": params or {},
        }
        resp = self._session.post(
            self.config.base_url,
            headers=self._headers(),
            json=payload,
            timeout=self.config.timeout,
        )
        if resp.status_code >= 300:
            raise RuntimeError(f"MCP server error {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(f"MCP error: {data.get('error')}")
        return data

    def initialize(self, client_info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._rpc("initialize", {"client": client_info or {}})

    def list_tools(self) -> Dict[str, Any]:
        return self._rpc("tools/list")

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._rpc("tools/call", {"name": name, "arguments": arguments or {}})

    def list_resources(self) -> Dict[str, Any]:
        return self._rpc("resources/list")

    def read_resource(self, uri: str) -> Dict[str, Any]:
        return self._rpc("resources/read", {"uri": uri})


class MCPServerStore:
    def __init__(self, root: str):
        self.root = root
        os.makedirs(self.root, exist_ok=True)

    def _path_for(self, owner: str) -> str:
        safe_owner = re.sub(r"[^A-Za-z0-9_.-]+", "_", owner or "default")
        return os.path.join(self.root, f"{safe_owner}.json")

    def list_servers(self, owner: str) -> List[MCPServerConfig]:
        path = self._path_for(owner)
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            return []
        items = payload.get("servers") if isinstance(payload, dict) else []
        if not isinstance(items, list):
            return []
        results = []
        for entry in items:
            if not isinstance(entry, dict):
                continue
            results.append(
                MCPServerConfig(
                    name=str(entry.get("name") or ""),
                    base_url=str(entry.get("base_url") or ""),
                    auth_type=str(entry.get("auth_type") or "bearer"),
                    auth_token=entry.get("auth_token"),
                    headers=entry.get("headers") if isinstance(entry.get("headers"), dict) else {},
                    timeout=int(entry.get("timeout") or 20),
                )
            )
        return results

    def save_server(self, owner: str, config: MCPServerConfig) -> None:
        servers = self.list_servers(owner)
        servers = [s for s in servers if s.name != config.name]
        servers.append(config)
        payload = {"servers": [s.__dict__ for s in servers]}
        path = self._path_for(owner)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)

    def delete_server(self, owner: str, name: str) -> bool:
        servers = self.list_servers(owner)
        next_servers = [s for s in servers if s.name != name]
        if len(next_servers) == len(servers):
            return False
        payload = {"servers": [s.__dict__ for s in next_servers]}
        path = self._path_for(owner)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        return True

    def get_server(self, owner: str, name: str) -> Optional[MCPServerConfig]:
        for server in self.list_servers(owner):
            if server.name == name:
                return server
        return None
