#!/usr/bin/env python3
"""Minimal 'az' shim for AzureCliCredential inside containers.

The .NET Azure Identity SDK's AzureCliCredential calls:
    az account get-access-token --output json --resource <resource>

This script reads the MSAL token cache (mounted from the host's ~/.azure)
and returns matching tokens — no full Azure CLI installation needed.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone


def get_config_dir():
    return os.environ.get("AZURE_CONFIG_DIR", os.path.expanduser("~/.azure"))


def load_json(path):
    for enc in ("utf-8-sig", "utf-8"):
        try:
            with open(path, encoding=enc) as f:
                return json.load(f)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    with open(path) as f:
        return json.load(f)


def _get_default_tenant(config_dir):
    """Return the tenant ID of the default subscription."""
    try:
        profile = load_json(os.path.join(config_dir, "azureProfile.json"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    for sub in profile.get("subscriptions", []):
        if sub.get("isDefault"):
            return sub.get("tenantId")
    return None


def cmd_get_access_token(args):
    resource = "https://management.azure.com"
    tenant_filter = None

    i = 0
    while i < len(args):
        if args[i] == "--resource" and i + 1 < len(args):
            resource = args[i + 1]
            i += 2
        elif args[i] == "--tenant" and i + 1 < len(args):
            tenant_filter = args[i + 1]
            i += 2
        else:
            i += 1

    config_dir = get_config_dir()
    cache_path = os.path.join(config_dir, "msal_token_cache.json")

    if not os.path.exists(cache_path):
        print(f"ERROR: MSAL token cache not found at {cache_path}", file=sys.stderr)
        sys.exit(1)

    cache = load_json(cache_path)
    resource_stripped = resource.rstrip("/")
    equivalent_resources = {resource_stripped}
    arm_resources = {
        "https://management.azure.com",
        "https://management.core.windows.net",
    }
    if resource_stripped in arm_resources:
        equivalent_resources = arm_resources
    now = time.time()

    default_tenant = tenant_filter or _get_default_tenant(config_dir)
    candidates = []

    for token_data in cache.get("AccessToken", {}).values():
        target = token_data.get("target", "")
        if not any(r in target for r in equivalent_resources):
            continue

        realm = token_data.get("realm", "")
        if tenant_filter and realm != tenant_filter:
            continue

        expires_on = int(token_data.get("expires_on", 0))
        if expires_on <= now:
            continue

        is_user_token = "user_impersonation" in target
        is_right_tenant = realm == default_tenant
        priority = (is_right_tenant, is_user_token, expires_on)
        candidates.append((priority, token_data, expires_on))

    if not candidates:
        print(
            "ERROR: No valid token found in MSAL cache. Run 'az login' on the host.",
            file=sys.stderr,
        )
        sys.exit(1)

    candidates.sort(key=lambda x: x[0], reverse=True)
    _, best_token, expires_on = candidates[0]

    sub_id = ""
    try:
        profile = load_json(os.path.join(config_dir, "azureProfile.json"))
        for sub in profile.get("subscriptions", []):
            if sub.get("isDefault") or sub.get("tenantId") == best_token.get("realm"):
                sub_id = sub.get("id", "")
                break
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    result = {
        "accessToken": best_token["secret"],
        "expiresOn": datetime.fromtimestamp(expires_on, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S.%f"
        ),
        "subscription": sub_id,
        "tenant": best_token.get("realm", ""),
        "tokenType": "Bearer",
    }
    json.dump(result, sys.stdout)
    sys.exit(0)


def cmd_account_show():
    config_dir = get_config_dir()
    try:
        profile = load_json(os.path.join(config_dir, "azureProfile.json"))
    except (FileNotFoundError, json.JSONDecodeError):
        print("ERROR: No Azure profile found.", file=sys.stderr)
        sys.exit(1)

    for sub in profile.get("subscriptions", []):
        if sub.get("isDefault"):
            json.dump(sub, sys.stdout)
            sys.exit(0)

    subs = profile.get("subscriptions", [])
    if subs:
        json.dump(subs[0], sys.stdout)
    else:
        print("ERROR: No subscriptions in profile.", file=sys.stderr)
        sys.exit(1)


def main():
    args = sys.argv[1:]

    if len(args) >= 2 and args[0] == "account":
        if args[1] == "get-access-token":
            cmd_get_access_token(args[2:])
            return
        if args[1] == "show":
            cmd_account_show()
            return

    print(f"az shim: unsupported command: {' '.join(args)}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
