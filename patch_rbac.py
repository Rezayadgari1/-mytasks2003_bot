"""Fix RBAC: per-manager permission overrides were stored but never enforced.

master_has_permission() only consulted role defaults, ignoring the
permissions_json that the manager-permissions UI writes. Revoking or granting
a permission for a specific manager therefore had no effect.
"""
p = 'bot.py'
src = open(p, encoding='utf-8').read()

old = '''    if role == "owner":
        return True
    return permission in MASTER_ROLE_PERMISSIONS.get(role,set())'''
new = '''    if role == "owner":
        return True
    # Per-manager overrides (edited from the manager permissions UI) win over
    # the role default. An empty list falls back to the role's defaults.
    try:
        c=db(); r=c.execute("SELECT permissions_json FROM management_roles WHERE user_id=? AND active=1",(int(uid),)).fetchone(); c.close()
        if r is not None:
            perms=set(json.loads(r["permissions_json"] or "[]"))
            if perms:
                return permission in perms
    except Exception:
        logger.exception("master_has_permission override lookup failed")
    return permission in MASTER_ROLE_PERMISSIONS.get(role,set())'''

assert src.count(old) == 1, 'master_has_permission body not found (count=%d)' % src.count(old)
src = src.replace(old, new)

with open(p, 'w', encoding='utf-8', newline='') as f:
    f.write(src)
print('rbac override enforcement OK')
