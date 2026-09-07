from pathlib import Path

BOT = Path('/app/bot.py')
s = BOT.read_text(encoding='utf-8')

MARKER = 'FORCED_SUB_MULTI_CHANNEL_V2'
if MARKER not in s:
    patch = r'''

# ===================== FORCED SUBSCRIPTION MULTI-CHANNEL V2 =====================
# FORCED_SUB_MULTI_CHANNEL_V2
# Live membership gate. Multiple required channels can be configured independently.
# Existing users/data are preserved. This layer is additive.

def _fsub_now():
    return datetime.now(TZ).isoformat()


def _fsub_ensure_table():
    c = None
    try:
        c = db()
        c.execute("""CREATE TABLE IF NOT EXISTS managed_channels(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
        c.commit()
    finally:
        if c is not None:
            c.close()


def _fsub_normalize_ref(raw):
    raw = str(raw or '').strip()
    if not raw:
        return ''
    if re.fullmatch(r'-?\d+', raw):
        return raw
    if re.fullmatch(r'@[A-Za-z0-9_]{5,32}', raw):
        return raw
    m = re.match(r'^https?://t\.me/([A-Za-z0-9_]{5,32})/?$', raw, re.I)
    if m:
        return '@' + m.group(1)
    m = re.match(r'^https?://t\.me/c/(\d+)(?:/\d+)?/?$', raw, re.I)
    if m:
        return '-100' + m.group(1)
    return ''


def _fsub_join_url(ref):
    ref = str(ref or '').strip()
    if ref.startswith('@'):
        return 'https://t.me/' + ref[1:]
    if ref.startswith('https://t.me/') or ref.startswith('http://t.me/'):
        return ref
    return ''


def _fsub_ref_for_api(ref):
    ref = _fsub_normalize_ref(ref) or str(ref or '').strip()
    if re.fullmatch(r'-?\d+', ref):
        return int(ref)
    return ref


def _fsub_channels(include_disabled=False):
    _fsub_ensure_table()
    c = db()
    try:
        sql = 'SELECT id,channel_id,title,enabled,created_at,updated_at FROM managed_channels'
        if not include_disabled:
            sql += ' WHERE enabled=1'
        sql += ' ORDER BY id'
        return c.execute(sql).fetchall()
    finally:
        c.close()


def _fsub_enabled():
    try:
        if '_forced_sub_is_enabled' in globals():
            return bool(_forced_sub_is_enabled())
    except Exception:
        pass
    return True


def _fsub_admin(uid):
    try:
        return int(uid) in ADMIN_IDS or bool(admin_guard(uid))
    except Exception:
        return int(uid) in ADMIN_IDS


def _fsub_status_is_member(member):
    status = getattr(member, 'status', None)
    value = getattr(status, 'value', status)
    value = str(value).lower() if value is not None else ''
    if value in {'member', 'administrator', 'creator', 'owner'}:
        return True
    if value == 'restricted':
        return bool(getattr(member, 'is_member', False))
    return False


async def _fsub_check_channel(bot, user_id, channel_ref):
    try:
        member = await bot.get_chat_member(chat_id=_fsub_ref_for_api(channel_ref), user_id=int(user_id))
        return _fsub_status_is_member(member)
    except Exception as exc:
        logger.error('Forced-sub membership check failed user=%s channel=%s: %s', user_id, channel_ref, exc)
        return False


async def _fsub_check_all(bot, user_id):
    """Return (ok, missing_channels). Membership is checked live on every gate."""
    if _fsub_admin(user_id):
        return True, []
    if not _fsub_enabled():
        return True, []

    channels = _fsub_channels(False)
    if channels:
        missing = []
        for row in channels:
            if not await _fsub_check_channel(bot, user_id, row['channel_id']):
                missing.append(row)
        return len(missing) == 0, missing

    if '_forced_sub_channel_ref' in globals():
        try:
            ref = _forced_sub_channel_ref()
            if ref and not await _fsub_check_channel(bot, user_id, ref):
                return False, [{'channel_id': str(ref), 'title': ''}]
        except Exception:
            return False, [{'channel_id': '', 'title': ''}]
    return True, []


def _fsub_gate_text(uid, missing):
    fa = lang(uid) == 'fa'
    if fa:
        lines = ['🔒 برای استفاده از امکانات ربات باید عضو همه کانال‌های زیر باشی:', '']
        for row in missing:
            lines.append('📢 ' + str(row['title'] or row['channel_id'] or 'کانال'))
        lines += ['', 'بعد از عضویت روی «✅ عضو شدم؛ بررسی کن» بزن.']
        return '\n'.join(lines)
    lines = ['🔒 To use the bot, join all of the channels below:', '']
    for row in missing:
        lines.append('📢 ' + str(row['title'] or row['channel_id'] or 'Channel'))
    lines += ['', 'After joining, press “✅ Check membership”.']
    return '\n'.join(lines)


def _fsub_keyboard(uid, missing=None):
    fa = lang(uid) == 'fa'
    rows = []
    items = missing if missing is not None else _fsub_channels(False)
    for row in items:
        ref = str(row['channel_id'] or '')
        url = _fsub_join_url(ref)
        title = str(row['title'] or ref or ('کانال' if fa else 'Channel'))
        if url:
            rows.append([InlineKeyboardButton(('📢 عضویت در ' if fa else '📢 Join ') + title, url=url)])
    if not rows:
        try:
            url = required_channel_url()
            if url:
                rows.append([InlineKeyboardButton('📢 عضویت در کانال' if fa else '📢 Join channel', url=url)])
        except Exception:
            pass
    rows.append([InlineKeyboardButton('✅ عضو شدم؛ بررسی کن' if fa else '✅ Check membership', callback_data='subcheck')])
    return InlineKeyboardMarkup(rows)


async def _fsub_enforce(update, context):
    uid = update.effective_user.id if update.effective_user else 0
    if _fsub_admin(uid):
        return True
    ok, missing = await _fsub_check_all(context.bot, uid)
    if ok:
        return True
    text = _fsub_gate_text(uid, missing)
    kb = _fsub_keyboard(uid, missing)
    if update.callback_query:
        try:
            await update.callback_query.answer('❌ ابتدا عضو کانال‌های الزامی شو.', show_alert=True)
        except Exception:
            pass
        if update.callback_query.message:
            await update.callback_query.message.reply_text(text, reply_markup=kb)
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb)
    return False


async def _forced_sub_enforce_async(uid, bot):
    ok, missing = await _fsub_check_all(bot, uid)
    if ok:
        return True, None
    return False, (_fsub_gate_text(uid, missing), _fsub_keyboard(uid, missing))


async def is_channel_member(bot, uid):
    ok, _missing = await _fsub_check_all(bot, uid)
    return ok


def subscription_keyboard():
    return _fsub_keyboard(0)


def _fsub_admin_keyboard(uid):
    fa = lang(uid) == 'fa'
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('➕ افزودن کانال اجباری' if fa else '➕ Add required channel', callback_data='v25:fsub:add')],
        [InlineKeyboardButton('📋 کانال‌های اجباری' if fa else '📋 Required channels', callback_data='v25:fsub:list')],
        [InlineKeyboardButton('🔄 وضعیت عضویت اجباری' if fa else '🔄 Forced subscription status', callback_data='v25:fsub:toggle')],
        [InlineKeyboardButton('⬅️ منوی مدیریت' if fa else '⬅️ Admin menu', callback_data='v25:admin')],
    ])


def _fsub_admin_text(uid):
    fa = lang(uid) == 'fa'
    enabled = _fsub_enabled()
    rows = _fsub_channels(True)
    status = 'فعال' if enabled else 'غیرفعال'
    if not fa:
        status = 'Enabled' if enabled else 'Disabled'
    lines = [
        ('🔐 عضویت اجباری چندکاناله' if fa else '🔐 Multi-channel forced subscription'),
        '',
        ('وضعیت: ' if fa else 'Status: ') + status,
        ('کانال‌های ثبت‌شده: ' + str(len(rows)) if fa else 'Configured channels: ' + str(len(rows))),
    ]
    if rows:
        lines.append('')
        for i, r in enumerate(rows, 1):
            lines.append(f"{'🟢' if r['enabled'] else '🔴'} {i}. {r['title'] or r['channel_id']}")
    return '\n'.join(lines)


async def _fsub_admin_show(update, context):
    uid = update.effective_user.id
    if not _fsub_admin(uid):
        return
    target = update.message or (update.callback_query.message if update.callback_query else None)
    if target:
        if update.callback_query:
            await target.edit_text(_fsub_admin_text(uid), reply_markup=_fsub_admin_keyboard(uid))
        else:
            await target.reply_text(_fsub_admin_text(uid), reply_markup=_fsub_admin_keyboard(uid))


async def _fsub_verify_bot_admin(bot, channel_ref):
    me = await bot.get_me()
    member = await bot.get_chat_member(chat_id=_fsub_ref_for_api(channel_ref), user_id=me.id)
    status = getattr(member, 'status', None)
    value = getattr(status, 'value', status)
    value = str(value).lower() if value is not None else ''
    return value in {'administrator', 'creator', 'owner'}


async def _fsub_add_channel(uid, bot, raw):
    ref = _fsub_normalize_ref(raw)
    if not ref:
        return False, '❌ شناسه یا لینک کانال معتبر نیست.\nنمونه: @mychannel یا -1001234567890 یا https://t.me/mychannel'
    try:
        chat = await bot.get_chat(chat_id=_fsub_ref_for_api(ref))
    except Exception:
        return False, '❌ کانال پیدا نشد یا ربات به آن دسترسی ندارد.\nاول ربات را داخل کانال اضافه کن و دوباره تلاش کن.'
    try:
        if not await _fsub_verify_bot_admin(bot, ref):
            return False, '❌ ربات در این کانال ادمین نیست.\nربات را به کانال اضافه کن و دسترسی Administrator بده، بعد دوباره ثبت کن.'
    except Exception:
        return False, '❌ وضعیت دسترسی ربات به کانال قابل بررسی نیست.\nربات باید داخل کانال Administrator باشد.'
    title = getattr(chat, 'title', None) or getattr(chat, 'username', None) or ref
    _fsub_ensure_table()
    c = db()
    try:
        now = _fsub_now()
        c.execute("""INSERT INTO managed_channels(channel_id,title,enabled,created_at,updated_at)
                     VALUES(?,?,?,?,?)
                     ON CONFLICT(channel_id) DO UPDATE SET title=excluded.title,enabled=1,updated_at=excluded.updated_at""",
                  (ref, str(title), 1, now, now))
        c.commit()
    finally:
        c.close()
    return True, f'✅ کانال «{title}» ثبت شد و عضویت اجباری برای آن فعال است.'


async def _fsub_list_callback(update, context):
    q = update.callback_query
    uid = q.from_user.id
    rows = _fsub_channels(True)
    fa = lang(uid) == 'fa'
    buttons = []
    for r in rows:
        state = '🟢' if r['enabled'] else '🔴'
        buttons.append([InlineKeyboardButton(f"{state} {r['title'] or r['channel_id']}", callback_data=f"v25:fsub:item:{r['id']}")])
    buttons.append([InlineKeyboardButton('⬅️ بازگشت' if fa else '⬅️ Back', callback_data='v25:fsub:home')])
    text = '📋 <b>کانال‌های اجباری</b>\n\n' if fa else '📋 <b>Required channels</b>\n\n'
    if not rows:
        text += 'هنوز کانالی ثبت نشده.' if fa else 'No channels configured yet.'
    else:
        text += '\n'.join(f"{i}. {r['title'] or r['channel_id']} {'🟢' if r['enabled'] else '🔴'}" for i, r in enumerate(rows, 1))
    await q.message.edit_text(text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(buttons))


async def _fsub_item_callback(update, context, row_id):
    q = update.callback_query
    uid = q.from_user.id
    c = db()
    row = c.execute('SELECT * FROM managed_channels WHERE id=?', (int(row_id),)).fetchone()
    c.close()
    if not row:
        await q.answer('کانال پیدا نشد.', show_alert=True)
        return
    fa = lang(uid) == 'fa'
    state = 'فعال' if row['enabled'] else 'غیرفعال'
    text = f"📢 <b>{html.escape(row['title'] or row['channel_id'])}</b>\n\nوضعیت: {state}" if fa else f"📢 <b>{html.escape(row['title'] or row['channel_id'])}</b>\n\nStatus: {state}"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('🔴 غیرفعال کن' if row['enabled'] else '🟢 فعال کن', callback_data=f"v25:fsub:enable:{row_id}:{0 if row['enabled'] else 1}")],
        [InlineKeyboardButton('🗑 حذف کانال' if fa else '🗑 Remove channel', callback_data=f"v25:fsub:delete:{row_id}")],
        [InlineKeyboardButton('⬅️ بازگشت' if fa else '⬅️ Back', callback_data='v25:fsub:list')],
    ])
    await q.message.edit_text(text, parse_mode='HTML', reply_markup=kb)


async def _fsub_delete_callback(update, context, row_id):
    q = update.callback_query
    c = db()
    c.execute('DELETE FROM managed_channels WHERE id=?', (int(row_id),))
    c.commit()
    c.close()
    await q.answer('✅ کانال حذف شد.', show_alert=True)
    await _fsub_list_callback(update, context)


async def _fsub_enable_callback(update, context, row_id, enabled):
    q = update.callback_query
    c = db()
    c.execute('UPDATE managed_channels SET enabled=?,updated_at=? WHERE id=?', (int(enabled), _fsub_now(), int(row_id)))
    c.commit()
    c.close()
    await q.answer('✅ وضعیت تغییر کرد.', show_alert=True)
    await _fsub_item_callback(update, context, row_id)


async def _fsub_toggle_callback(update, context):
    q = update.callback_query
    current = _fsub_enabled()
    if '_forced_sub_set' in globals():
        _forced_sub_set('forced_sub_enabled', '0' if current else '1')
    else:
        c = db()
        c.execute("INSERT INTO system_settings(key,value,updated_at) VALUES('forced_sub_enabled',?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at", ('0' if current else '1', _fsub_now()))
        c.commit()
        c.close()
    await q.answer('✅ وضعیت عضویت اجباری تغییر کرد.', show_alert=True)
    await q.message.edit_text(_fsub_admin_text(q.from_user.id), reply_markup=_fsub_admin_keyboard(q.from_user.id))


# Route new forced-sub callbacks through the existing V25 callback chain.
_OLD_V25_CALLBACK_FOR_FSUB = globals().get('v25_callback')
async def v25_callback(update, context):
    q = update.callback_query
    data = (q.data or '') if q else ''
    if data == 'v25:fsub:home':
        await q.answer()
        return await _fsub_admin_show(update, context)
    if data == 'v25:fsub:list':
        await q.answer()
        return await _fsub_list_callback(update, context)
    if data == 'v25:fsub:add':
        await q.answer()
        if not _fsub_admin(q.from_user.id):
            return
        context.user_data['fsub_add_channel'] = True
        await q.message.reply_text('➕ شناسه یا لینک کانال را بفرست.\nمثال: @mychannel یا -1001234567890 یا https://t.me/mychannel')
        return
    if data == 'v25:fsub:toggle':
        await q.answer()
        return await _fsub_toggle_callback(update, context)
    if data.startswith('v25:fsub:item:'):
        await q.answer()
        return await _fsub_item_callback(update, context, data.rsplit(':', 1)[1])
    if data.startswith('v25:fsub:delete:'):
        await q.answer()
        return await _fsub_delete_callback(update, context, data.rsplit(':', 1)[1])
    if data.startswith('v25:fsub:enable:'):
        await q.answer()
        parts = data.split(':')
        return await _fsub_enable_callback(update, context, parts[3], parts[4])
    if _OLD_V25_CALLBACK_FOR_FSUB:
        return await _OLD_V25_CALLBACK_FOR_FSUB(update, context)


# Admin reply-keyboard entry.
_OLD_TEXT_ROUTER_FOR_FSUB = globals().get('text_router')
async def text_router(update, context):
    uid = update.effective_user.id if update.effective_user else 0
    txt = (update.message.text or '').strip() if update.message else ''
    if _fsub_admin(uid) and txt in {'📢 مدیریت کانال', '📢 Channel Management'}:
        return await _fsub_admin_show(update, context)
    if _fsub_admin(uid) and context.user_data.get('fsub_add_channel') and update.message:
        context.user_data.pop('fsub_add_channel', None)
        _ok, msg = await _fsub_add_channel(uid, context.bot, txt)
        await update.message.reply_text(msg, reply_markup=_fsub_admin_keyboard(uid))
        return
    if _OLD_TEXT_ROUTER_FOR_FSUB:
        return await _OLD_TEXT_ROUTER_FOR_FSUB(update, context)


# Universal callback gate. It wraps every CallbackQueryHandler registered by the app.
try:
    _ORIGINAL_ADD_HANDLER_FSUB = Application.add_handler
    if not getattr(Application.add_handler, '_fsub_wrapped', False):
        def _fsub_add_handler(self, handler, group=0):
            if isinstance(handler, CallbackQueryHandler):
                original_callback = handler.callback
                async def gated_callback(update, context, _orig=original_callback):
                    uid = update.effective_user.id if update.effective_user else 0
                    data = (update.callback_query.data or '') if update.callback_query else ''
                    if not _fsub_admin(uid) and data != 'subcheck':
                        if not await _fsub_enforce(update, context):
                            return
                    return await _orig(update, context)
                handler.callback = gated_callback
            return _ORIGINAL_ADD_HANDLER_FSUB(self, handler, group)
        _fsub_add_handler._fsub_wrapped = True
        Application.add_handler = _fsub_add_handler
except Exception:
    logger.exception('Failed to install universal forced-sub callback gate')


# /start must also respect live membership before showing the main menu.
_OLD_START_FOR_FSUB = globals().get('start')
if _OLD_START_FOR_FSUB:
    async def start(update, context):
        uid = update.effective_user.id if update.effective_user else 0
        if not _fsub_admin(uid):
            ok, missing = await _fsub_check_all(context.bot, uid)
            if not ok:
                await update.message.reply_text(_fsub_gate_text(uid, missing), reply_markup=_fsub_keyboard(uid, missing))
                return
        return await _OLD_START_FOR_FSUB(update, context)


# Every ordinary text update goes through the live membership gate.
_PREVIOUS_TEXT_ROUTER_FINAL = globals().get('text_router')
async def text_router(update, context):
    uid = update.effective_user.id if update.effective_user else 0
    if not _fsub_admin(uid):
        if not await _fsub_enforce(update, context):
            return
    if _PREVIOUS_TEXT_ROUTER_FINAL:
        return await _PREVIOUS_TEXT_ROUTER_FINAL(update, context)
'''
    s += patch
    BOT.write_text(s, encoding='utf-8')
    print('forced subscription multi-channel patch applied')
else:
    print('forced subscription patch already present')
