from pathlib import Path

BOT = Path('/app/bot.py')
s = BOT.read_text(encoding='utf-8')

if 'FEATURE_CONTROL_FINAL_V3' not in s:
    code = r'''# FEATURE_CONTROL_FINAL_V3
# Final cleanup for the admin switchboard and navigation-safe feature gating.

# Navigation must remain available even when the feature itself is disabled.
_FC_FINAL_V2_CALLBACK_KEY = _fc_callback_key

def _fc_callback_key(data):
    data = str(data or '')
    if data in ('aichat:back', 'nav:main', 'v25:fc:categories'):
        return None
    return _FC_FINAL_V2_CALLBACK_KEY(data)

# Route the old admin feature entry point to the new categorized switchboard.
async def _fc_admin_legacy_router(update, context):
    q = getattr(update, 'callback_query', None)
    data = str(q.data or '') if q else ''
    uid = update.effective_user.id if update.effective_user else 0
    if not _fc_admin(uid):
        return False
    if data == 'v25:adminfeat:panel':
        return await _fc_admin_categories(update, context)
    if data.startswith('v25:adminfeat:toggle:'):
        key = data.split('v25:adminfeat:toggle:', 1)[1].strip()
        key = _fc_key(key)
        for idx, (_title, items) in enumerate(FEATURE_CONTROL_CATEGORIES):
            if any(k == key for k, _label in items):
                try:
                    current = bool(feature_enabled(key)) if 'feature_enabled' in globals() else True
                    set_feature(key, not current)
                except Exception:
                    logger.exception('Legacy feature toggle failed: %s', key)
                return await _fc_admin_category(update, context, idx)
        return await _fc_admin_categories(update, context)
    return False

_FC_FINAL_V2_V25 = v25_callback
async def v25_callback(update, context):
    if await _fc_admin_legacy_router(update, context):
        return True
    return await _FC_FINAL_V2_V25(update, context)

# Re-wrap the named AI callback after the callback-key cleanup above.
try:
    if 'ai_chat_navigation_callback' in globals():
        _fc_ai_nav_old = ai_chat_navigation_callback
        async def ai_chat_navigation_callback(update, context, *args, **kwargs):
            return await _fc_ai_nav_old(update, context, *args, **kwargs)
except Exception:
    pass
'''
    s += '\n\n' + code
    BOT.write_text(s, encoding='utf-8')
    print('Final feature control v3 applied')
else:
    print('Final feature control v3 already present')
