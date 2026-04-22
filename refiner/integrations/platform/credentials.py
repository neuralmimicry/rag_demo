import os
import re
import sys
from typing import Optional, Tuple


def _can_prompt() -> bool:
    """Return True when stdin is interactive enough for credential prompts."""
    try:
        return bool(sys.stdin) and sys.stdin.isatty()
    except Exception:
        return False


def get_credentials(instance_name: Optional[str] = None, allow_prompt: bool = True) -> Tuple[str, str]:
    """
    Retrieves JIRA credentials from environment variables or prompts the user.
    Supports instance-specific overrides via JIRA_USERNAME_NAME and JIRA_PASSWORD_NAME.
    :param instance_name: Optional name of the instance to look for specific credentials.
    :return: A tuple containing the username and password.
    """
    if instance_name:
        # Normalize name for env var (e.g. "My Instance" -> "MY_INSTANCE")
        suffix = re.sub(r'[^A-Z0-9]', '_', str(instance_name).upper())
        u = os.getenv(f"JIRA_USERNAME_{suffix}")
        p = os.getenv(f"JIRA_PASSWORD_{suffix}")
        if u and p:
            return u, p

    username = os.getenv("JIRA_USERNAME") or ""
    password = os.getenv("JIRA_PASSWORD") or ""

    if not allow_prompt or not _can_prompt():
        return username, password

    if not username:
        try:
            username = input("Enter your JIRA username: ")
        except (EOFError, KeyboardInterrupt, OSError):
            username = ""

    if not password:
        try:
            import getpass
            password = getpass.getpass("Enter your JIRA password or API token: ")
        except (EOFError, KeyboardInterrupt, OSError):
            password = ""

    return username, password


def get_llm_credentials(name: Optional[str] = None, provider_type: str = "openai"):
    """
    Retrieves LLM credentials (API key, etc) from environment variables.
    Supports name-specific overrides via TYPE_API_KEY_NAME.
    """
    if name:
        suffix = re.sub(r'[^A-Z0-9]', '_', str(name).upper())
        pt = str(provider_type).lower()
        if pt in ("openai", "gpt", "chatgpt"):
            key = os.getenv(f"OPENAI_API_KEY_{suffix}")
            if key:
                return key
        elif pt in ("gemini", "google"):
            key = os.getenv(f"GEMINI_API_KEY_{suffix}")
            if key:
                return key
            token = os.getenv(f"GEMINI_ACCESS_TOKEN_{suffix}")
            if token:
                return token
        elif pt == "ollama":
            url = os.getenv(f"OLLAMA_BASE_URL_{suffix}")
            if url:
                return url

    # Fallbacks to standard environment variables
    pt = str(provider_type).lower()
    if pt in ("openai", "gpt", "chatgpt"):
        return os.getenv("OPENAI_API_KEY")
    elif pt in ("gemini", "google"):
        return os.getenv("GEMINI_API_KEY") or os.getenv("GEMINI_ACCESS_TOKEN") or os.getenv("GOOGLE_ACCESS_TOKEN")
    elif pt == "ollama":
        return os.getenv("OLLAMA_BASE_URL")
    return None


def get_search_credentials(name: Optional[str] = None):
    """
    Retrieves Search engine credentials (API key, CSE ID) from environment variables.
    Supports name-specific overrides via GOOGLE_API_KEY_NAME and GOOGLE_CSE_ID_NAME.
    """
    if name:
        suffix = re.sub(r'[^A-Z0-9]', '_', str(name).upper())
        key = os.getenv(f"GOOGLE_API_KEY_{suffix}")
        cse = os.getenv(f"GOOGLE_CSE_ID_{suffix}")
        if key and cse:
            return key, cse

    key = os.getenv("GOOGLE_API_KEY")
    cse = os.getenv("GOOGLE_CSE_ID")
    return key, cse


def get_search_api_key(provider_type: str, name: Optional[str] = None) -> Optional[str]:
    """
    Retrieve single-key search provider credentials from environment variables.
    Supports provider/name-specific overrides for Brave and Tavily.
    """
    provider = str(provider_type or "").strip().lower()
    if provider not in {"brave", "tavily"}:
        return None
    if name:
        suffix = re.sub(r'[^A-Z0-9]', '_', str(name).upper())
        if provider == "brave":
            return os.getenv(f"BRAVE_SEARCH_API_KEY_{suffix}") or os.getenv(f"BRAVE_API_KEY_{suffix}")
        return os.getenv(f"TAVILY_API_KEY_{suffix}")
    if provider == "brave":
        return os.getenv("BRAVE_SEARCH_API_KEY") or os.getenv("BRAVE_API_KEY")
    return os.getenv("TAVILY_API_KEY")
