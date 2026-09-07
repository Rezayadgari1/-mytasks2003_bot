from pathlib import Path

BOT = Path('/app/bot.py')
s = BOT.read_text(encoding='utf-8')

MARKER = 'FORCED_SUB_MULTI_CHANNEL_V2_BULK'
if MARKER not in s:
    patch = r'''

# ===================== FORCED SUBSCRIPTION V2 BULK / MIGRATION =====================
# FORCED_SUB_MULTI_CHANNEL_V2_BULK

# Preserve the channel configured by the older single-channel system when the
# new managed-channel table is first used. This prevents adding a new channel
# from silently replacing the old required channel.
_OLD_FSUB_CHANNELS_V2 = _fsub_channels

def _fsub_channels(include_disabled=False):
    rows = _OLD_FSUB_CHANNELS_V2(include_disabled)
    if rows:
        return rows
    try:
        legacy_ref = _forced_sub_channel_ref() if '_forced_sub_channel_ref' in globals() else ''
        if legacy_ref:
            _fsub_ensure_table()
            c = db()
            try:
                now = _fsub_now()
                c.execute("""INSERT OR IGNORE INTO managed_channels(channel_id,title,enabled,created_at,updated_at)
                             VALUES(?,?,?,?,?)""", (str(legacy_ref), str(legacy_ref), 1, now, now))
                c.commit()
            finally:
                c.close()
            return _OLD_FSUB_CHANNELS_V2(include_disabled)
    except Exception:
        logger.exception('Failed to migrate legacy required channel')
    return rows


# Allow the admin to paste several channels at once. Accepted separators are
# newline, comma or semicolon. Each channel is still verified independently.
_OLD_TEXT_ROUTER_FSUB_BULK = text_router
async def text_router(update, context):
    uid = update.effective_user.id if update.effective_user else 0
    if _fsub_admin(uid) and context.user_data.get('fsub_add_channel') and update.message:
        context.user_data.pop('fsub_add_channel', None)
        raw = (update.message.text or '').strip()
        items = [x.strip() for x in re.split(r'[\n,;]+', raw) if x.strip()]
        if not items:
            await update.message.reply_text('❌ حداقل یک کانال وارد کن.', reply_markup=_fsub_admin_keyboard(uid))
            return
        if len(items) > 20:
            items = items[:20]
        results = []
        for item in items:
            ok, msg = await _fsub_add_channel(uid, context.bot, item)
            results.append(('✅ ' if ok else '❌ ') + msg.replace('✅ ', '').replace('❌ ', ''))
        await update.message.reply_text('\n\n'.join(results), reply_markup=_fsub_admin_keyboard(uid))
        return
    return await _OLD_TEXT_ROUTER_FSUB_BULK(update, context)
'''
    s += patch
    BOT.write_text(s, encoding='utf-8')
    print('forced subscription bulk/migration patch applied')
else:
    print('forced subscription bulk/migration patch already present')
