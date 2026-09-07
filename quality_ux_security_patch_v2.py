from pathlib import Path
P=Path('/app/bot.py')
s=P.read_text(encoding='utf-8')
MARK='QUALITY_UX_SECURITY_FINAL_V2'
if MARK not in s:
    s += r'''

# ===================== QUALITY / UX / SECURITY FINAL V2 =====================
# QUALITY_UX_SECURITY_FINAL_V2
# Close direct compact-menu callback paths so disabled features cannot execute.

_QUALITY_OLD_COMPACT_MENU_CALLBACK = compact_menu_callback
async def compact_menu_callback(update, context):
    q=update.callback_query
    uid=q.from_user.id if q and q.from_user else 0
    data=(q.data or '') if q else ''
    fa=lang(uid)=='fa'
    feature=None
    if data=='cm:ai': feature='ai'
    elif data=='cm:voice': feature='voice'
    elif data=='cm:prices': feature='price_data'
    elif data=='cm:vip': feature='vip'
    elif data=='cm:referral': feature='referrals'
    elif data=='cm:settings': feature='settings'
    if feature and not _q_enabled(uid,feature):
        await q.answer('⛔ این قابلیت توسط مدیر غیرفعال شده است.' if fa else '⛔ This feature is disabled by the admin.',show_alert=True)
        return
    return await _QUALITY_OLD_COMPACT_MENU_CALLBACK(update,context)
'''
    P.write_text(s,encoding='utf-8')
    print('compact callback bypass closed')
else:
    print('already applied')
