from pathlib import Path

BOT=Path('/app/bot.py')
s=BOT.read_text(encoding='utf-8')
MARK='QUALITY_UX_SECURITY_FINAL_V1'
if MARK not in s:
    patch=r'''

# ===================== QUALITY / UX / SECURITY FINAL V1 =====================
# QUALITY_UX_SECURITY_FINAL_V1
# Final layer: clean user navigation, daily rotating copy, live feature gates,
# and safer forced-subscription bot setup. No user data is deleted or migrated backward.

# ---------- Friendly daily copy ----------
_DAILY_MORNING_FA = [
    "☀️ صبح بخیر {name} عزیز!\n\nامروز قرار نیست همه‌چیز را یک‌جا انجام بدی. فقط با یک قدم کوچیک شروع کنیم. 🌱",
    "🌤️ صبح قشنگت بخیر {name}!\n\nیه هدف کوچیک برای امروز انتخاب کن و با هم جلو بریم. 💪",
    "☀️ سلام {name} جان!\n\nروز تازه شروع شده. آماده‌ای امروز فقط یک درصد بهتر از دیروز باشی؟ 🚀",
    "🌞 صبح بخیر {name}!\n\nمن اینجام که کارها رو برات ساده‌تر کنیم. از اولین هدف شروع کنیم؟ 🎯",
    "🌷 صبح بخیر {name} عزیز!\n\nلازم نیست کامل باشی. فقط شروع کن، قدم بعدی خودش میاد. 💚",
    "☀️ روزت بخیر {name}!\n\nامروز یه فرصت تازه‌ست برای نزدیک‌تر شدن به چیزی که می‌خوای. ✨",
    "🌅 صبح بخیر {name} جان!\n\nبریم یه روز خوب و جمع‌وجور بسازیم؟ اولویت امروزت رو انتخاب کن. 🎯",
]
_DAILY_NIGHT_FA = [
    "🌙 شب بخیر {name} عزیز!\n\nقبل از خواب، یک لحظه به کارهایی که امروز انجام دادی افتخار کن. فردا دوباره با هم ادامه می‌دیم. 💚",
    "🌙 شب آرومت بخیر {name}!\n\nحتی یک قدم کوچیک هم پیشرفته. امشب با خیال راحت استراحت کن. 😴",
    "✨ شب بخیر {name} جان!\n\nامروز هرچقدر هم که پیش رفتی، ارزشمنده. فردا یک شروع تازه داریم. 🌱",
    "🌙 وقت استراحته {name}!\n\nگوشی رو کم‌کم کنار بذار، نفس عمیق بکش و برای فردا آماده شو. 💤",
    "🌃 شب بخیر {name} عزیز!\n\nسه چیز خوب امروزت رو یادت بیار و با خیال راحت بخواب. 💫",
    "🌙 خسته نباشی {name}!\n\nامروز تموم شد. خودت رو سرزنش نکن، فردا دوباره فرصت داریم. 🤍",
    "🌙 شبت آروم {name} جان!\n\nهدف‌ها فرار نمی‌کنن. امشب استراحت کن تا فردا پرانرژی‌تر برگردیم. 🔋",
]
_DAILY_MORNING_EN = [
    "☀️ Good morning {name}!\n\nYou don't need to do everything today. Let's start with one small step. 🌱",
    "🌤️ Morning, {name}!\n\nPick one small goal and let's move forward together. 💪",
    "☀️ Hi {name}!\n\nA fresh day is here. Ready to be one percent better than yesterday? 🚀",
    "🌞 Good morning {name}!\n\nI'm here to make things easier. Shall we start with your first goal? 🎯",
    "🌷 Good morning {name}!\n\nYou don't have to be perfect. Just start, and take the next step. 💚",
    "☀️ Have a great day, {name}!\n\nToday is another chance to get closer to what matters to you. ✨",
    "🌅 Morning, {name}!\n\nLet's build a good, simple day. Choose your first priority. 🎯",
]
_DAILY_NIGHT_EN = [
    "🌙 Good night, {name}!\n\nTake a moment to appreciate what you did today. We'll continue tomorrow. 💚",
    "🌙 Rest well, {name}!\n\nEven one small step counts. Give yourself a quiet night. 😴",
    "✨ Good night, {name}!\n\nWhatever you achieved today matters. Tomorrow is another fresh start. 🌱",
    "🌙 Time to rest, {name}!\n\nPut the phone aside, breathe deeply, and get ready for tomorrow. 💤",
    "🌃 Good night, {name}!\n\nRemember three good things from today and sleep easy. 💫",
    "🌙 You did well today, {name}!\n\nDon't blame yourself. Tomorrow gives us another chance. 🤍",
    "🌙 Sleep well, {name}!\n\nYour goals can wait. Rest tonight and come back stronger tomorrow. 🔋",
]

def _daily_variant(items, salt=''):
    # Stable for the whole local day, different on subsequent days.
    import hashlib as _qhash
    key=datetime.now(TZ).strftime('%Y-%m-%d')+'|'+salt
    return items[int(_qhash.sha256(key.encode('utf-8')).hexdigest()[:8],16) % len(items)]

try:
    if isinstance(T, dict) and 'fa' in T and 'en' in T:
        T['fa']['morning'] = _daily_variant(_DAILY_MORNING_FA, 'morning')
        T['en']['morning'] = _daily_variant(_DAILY_MORNING_EN, 'morning')
        T['fa']['night'] = _daily_variant(_DAILY_NIGHT_FA, 'night')
        T['en']['night'] = _daily_variant(_DAILY_NIGHT_EN, 'night')
except Exception:
    logger.exception('Daily greeting copy initialization failed')

# ---------- Clean categorized main menu ----------
def _q_enabled(uid,key):
    try:
        if uid in ADMIN_IDS:
            return True
        if 'feature_access_mode' in globals() and feature_access_mode(key) == 'off':
            return False
        if 'feature_enabled' in globals() and not feature_enabled(key):
            return False
        if 'user_feature_allowed' in globals() and not user_feature_allowed(uid,key):
            return False
    except Exception:
        # Fail closed for a feature check. Navigation remains available.
        return False
    return True

def _q_main_inline(uid):
    fa=lang(uid)=='fa'
    rows=[]
    def add(label,key,cb):
        if _q_enabled(uid,key):
            rows.append([InlineKeyboardButton(label,callback_data=cb)])
    if fa:
        add('🎯 اهداف و برنامه','goals','qmenu:goals')
        if _q_enabled(uid,'weekly') or _q_enabled(uid,'stats'):
            rows.append([InlineKeyboardButton('📊 پیشرفت و آمار',callback_data='qmenu:reports')])
        if _q_enabled(uid,'ai') or _q_enabled(uid,'voice') or _q_enabled(uid,'price_data'):
            rows.append([InlineKeyboardButton('🛠 ابزارهای هوشمند',callback_data='qmenu:tools')])
        if _q_enabled(uid,'vip') or _q_enabled(uid,'xp'):
            rows.append([InlineKeyboardButton('💎 VIP و پاداش‌ها',callback_data='qmenu:rewards')])
        if _q_enabled(uid,'profile') or _q_enabled(uid,'referrals') or _q_enabled(uid,'settings'):
            rows.append([InlineKeyboardButton('👤 حساب و تنظیمات',callback_data='qmenu:account')])
        if _q_enabled(uid,'support'):
            rows.append([InlineKeyboardButton('🎫 پشتیبانی',callback_data='qmenu:support')])
    else:
        add('🎯 Goals & Plan','goals','qmenu:goals')
        if _q_enabled(uid,'weekly') or _q_enabled(uid,'stats'):
            rows.append([InlineKeyboardButton('📊 Progress & Stats',callback_data='qmenu:reports')])
        if _q_enabled(uid,'ai') or _q_enabled(uid,'voice') or _q_enabled(uid,'price_data'):
            rows.append([InlineKeyboardButton('🛠 Smart Tools',callback_data='qmenu:tools')])
        if _q_enabled(uid,'vip') or _q_enabled(uid,'xp'):
            rows.append([InlineKeyboardButton('💎 VIP & Rewards',callback_data='qmenu:rewards')])
        if _q_enabled(uid,'profile') or _q_enabled(uid,'referrals') or _q_enabled(uid,'settings'):
            rows.append([InlineKeyboardButton('👤 Account & Settings',callback_data='qmenu:account')])
        if _q_enabled(uid,'support'):
            rows.append([InlineKeyboardButton('🎫 Support',callback_data='qmenu:support')])
    return InlineKeyboardMarkup(rows)


def _q_section(uid,section):
    fa=lang(uid)=='fa'; rows=[]
    def b(label,key,cb):
        if _q_enabled(uid,key): rows.append([InlineKeyboardButton(label,callback_data=cb)])
    if section=='goals':
        if fa:
            b('🎯 اهداف امروز','goals','goals:today'); b('➕ هدف جدید','goals','new_goal'); b('🏆 اهداف آماده','goals','goals:ready'); b('✏️ ویرایش اهداف','edit','cm:edit_goals'); b('📅 جدول هفتگی','weekly','goals:weekly')
        else:
            b("🎯 Today's Goals",'goals','goals:today'); b('➕ New Goal','goals','new_goal'); b('🏆 Ready Goals','goals','goals:ready'); b('✏️ Edit Goals','edit','cm:edit_goals'); b('📅 Weekly Table','weekly','goals:weekly')
    elif section=='reports':
        if fa:
            b('📊 آمار من','stats','cm:stats'); b('📅 جدول هفتگی','weekly','cm:weekly'); b('🏆 دستاوردها','achievements','cm:achievements'); b('⭐ XP','xp','cm:xp')
        else:
            b('📊 My Stats','stats','cm:stats'); b('📅 Weekly Table','weekly','cm:weekly'); b('🏆 Achievements','achievements','cm:achievements'); b('⭐ XP','xp','cm:xp')
    elif section=='tools':
        if fa:
            b('🤖 چت با AI','ai','cm:ai'); b('🎙️ دستیار صوتی','voice','cm:voice'); b('📈 قیمت آنلاین','price_data','cm:prices')
        else:
            b('🤖 AI Chat','ai','cm:ai'); b('🎙️ Voice Assistant','voice','cm:voice'); b('📈 Live Prices','price_data','cm:prices')
    elif section=='rewards':
        if fa:
            b('💎 VIP','vip','cm:vip'); b('⭐ XP','xp','cm:xp'); b('🎁 پاداش‌های من','xp','cm:xp'); b('🤝 دعوت دوستان','referrals','cm:referral')
        else:
            b('💎 VIP','vip','cm:vip'); b('⭐ XP','xp','cm:xp'); b('🎁 My Rewards','xp','cm:xp'); b('🤝 Invite Friends','referrals','cm:referral')
    elif section=='account':
        if fa:
            b('👤 پروفایل','profile','cm:profile'); b('🤝 دعوت دوستان','referrals','cm:referral'); b('⚙️ تنظیمات','settings','cm:settings')
        else:
            b('👤 Profile','profile','cm:profile'); b('🤝 Invite Friends','referrals','cm:referral'); b('⚙️ Settings','settings','cm:settings')
    elif section=='support':
        b('🎫 پشتیبانی','support','cm:support')
    rows.append([InlineKeyboardButton('🏠 منوی اصلی' if fa else '🏠 Main Menu',callback_data='qmenu:home')])
    return InlineKeyboardMarkup(rows)


def _q_menu_text(uid,section):
    fa=lang(uid)=='fa'
    titles={'goals':('🎯 <b>اهداف و برنامه</b>','🎯 <b>Goals & Plan</b>'),'reports':('📊 <b>پیشرفت و آمار</b>','📊 <b>Progress & Stats</b>'),'tools':('🛠 <b>ابزارهای هوشمند</b>','🛠 <b>Smart Tools</b>'),'rewards':('💎 <b>VIP و پاداش‌ها</b>','💎 <b>VIP & Rewards</b>'),'account':('👤 <b>حساب و تنظیمات</b>','👤 <b>Account & Settings</b>'),'support':('🎫 <b>پشتیبانی</b>','🎫 <b>Support</b>')}
    return titles[section][0 if fa else 1]


def _q_clean_keyboard(uid):
    # Reply keyboard is deliberately tiny. Detailed navigation lives in inline menus.
    fa=lang(uid)=='fa'
    return ReplyKeyboardMarkup([["🏠 منوی اصلی" if fa else "🏠 Main Menu"]],resize_keyboard=True,one_time_keyboard=False)

# Make the single visible ReplyKeyboard entry the home action. The inline menu exposes everything.
keyboard=_q_clean_keyboard
compact_keyboard=_q_clean_keyboard

_OLD_TEXT_ROUTER_QUALITY = text_router
async def text_router(update,context):
    if not update.message or not update.message.text:
        return await _OLD_TEXT_ROUTER_QUALITY(update,context)
    uid=update.effective_user.id; txt=update.message.text.strip(); fa=lang(uid)=='fa'
    if txt in ('🏠 منوی اصلی','🏠 Main Menu'):
        clear_flow(context)
        await update.message.reply_text('🏠 <b>منوی اصلی</b>' if fa else '🏠 <b>Main Menu</b>',parse_mode='HTML',reply_markup=_q_main_inline(uid))
        return
    if txt in ('🤖 چت با AI','🤖 AI Chat') and not _q_enabled(uid,'ai'):
        clear_flow(context); await update.message.reply_text('⛔ این قابلیت فعلاً در دسترس نیست.' if fa else '⛔ This feature is currently unavailable.',reply_markup=_q_clean_keyboard(uid)); return
    return await _OLD_TEXT_ROUTER_QUALITY(update,context)

_OLD_V25_CALLBACK_QUALITY = v25_callback
async def v25_callback(update,context):
    q=update.callback_query; data=q.data or ''; uid=q.from_user.id; fa=lang(uid)=='fa'
    if data.startswith('qmenu:'):
        await q.answer()
        section=data.split(':',1)[1]
        if section=='home':
            await q.message.edit_text('🏠 <b>منوی اصلی</b>' if fa else '🏠 <b>Main Menu</b>',parse_mode='HTML',reply_markup=_q_main_inline(uid)); return
        if section in ('goals','reports','tools','rewards','account','support'):
            await q.message.edit_text(_q_menu_text(uid,section),parse_mode='HTML',reply_markup=_q_section(uid,section)); return
    # Hard gate for all final smart-tool callbacks. Disabled means not executable.
    feature=None
    if data.startswith('aichat:') or data in ('cm:ai','v25:ai'):
        feature='ai'
    elif data in ('cm:voice','v25:voice') or data.startswith('voice:'):
        feature='voice'
    elif data.startswith('price:') or data in ('cm:prices','prices:menu'):
        feature='price_data'
    elif data.startswith('vip:') or data=='cm:vip': feature='vip'
    elif data.startswith('ref:') or data=='cm:referral': feature='referrals'
    if feature and not _q_enabled(uid,feature):
        await q.answer('⛔ این قابلیت توسط مدیر غیرفعال شده است.' if fa else '⛔ This feature is disabled by the admin.',show_alert=True)
        return
    # Legacy forced-sub admin entry gets the improved bot setup screen.
    if data in ('v25:fsub:panel','forcedsub:home') and _fsub_admin(uid):
        await q.answer()
        await q.message.edit_text(_fsub_admin_text(uid),parse_mode='HTML',reply_markup=_q_fsub_admin_keyboard(uid)); return
    if data=='v25:fsub:setbot' and _fsub_admin(uid):
        await q.answer()
        try:
            me=await context.bot.get_me()
            ok=True
            try: status=await context.bot.get_chat_member(me.id,me.id)
            except Exception: status=None
            name='@'+me.username if getattr(me,'username',None) else str(me.id)
            text=('🤖 <b>ست‌کردن ربات</b>\n\n'
                  f'ربات فعال: <b>{html.escape(name)}</b>\n'
                  'این همان رباتی است که در حال اجراست. برای عضویت اجباری، همین ربات باید داخل هر کانال Administrator باشد.\n\n'
                  'توکن ربات را داخل چت ارسال نکن. فقط ربات فعلی را به کانال اضافه کن و دسترسی Administrator بده.'
                  if fa else
                  '🤖 <b>Bot Setup</b>\n\n'
                  f'Running bot: <b>{html.escape(name)}</b>\n'
                  'For forced subscription, this running bot must be an Administrator in every required channel.\n\n'
                  'Never send the bot token in chat. Add the running bot to the channel and grant Administrator access.')
        except Exception:
            text='❌ وضعیت ربات قابل بررسی نیست.' if fa else '❌ Bot status could not be checked.'
        await q.message.edit_text(text,parse_mode='HTML',reply_markup=_q_fsub_admin_keyboard(uid)); return
    return await _OLD_V25_CALLBACK_QUALITY(update,context)


def _q_fsub_admin_keyboard(uid):
    fa=lang(uid)=='fa'
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('➕ افزودن کانال اجباری' if fa else '➕ Add required channel',callback_data='v25:fsub:add')],
        [InlineKeyboardButton('📋 کانال‌های اجباری' if fa else '📋 Required channels',callback_data='v25:fsub:list')],
        [InlineKeyboardButton('🤖 ست‌کردن و بررسی ربات' if fa else '🤖 Set up / verify bot',callback_data='v25:fsub:setbot')],
        [InlineKeyboardButton('🔄 فعال / غیرفعال' if fa else '🔄 Enable / Disable',callback_data='v25:fsub:toggle')],
        [InlineKeyboardButton('⬅️ منوی مدیریت' if fa else '⬅️ Admin menu',callback_data='v25:admin')],
    ])

# Keep forced-subscription checks live. The existing V2 layer checks every configured
# channel on every gated action. This wrapper also prevents stale membership state from
# being trusted if a user leaves after a previous successful check.
async def _quality_live_subscription_gate(update,context):
    uid=update.effective_user.id if update.effective_user else 0
    if _fsub_admin(uid): return True
    ok,_missing=await _fsub_check_all(context.bot,uid)
    return ok

# Add the new bot-setup entry to the existing admin panel keyboard without changing its other controls.
try:
    _old_q_fsub_keyboard=_fsub_admin_keyboard
    def _fsub_admin_keyboard(uid):
        base=_old_q_fsub_keyboard(uid)
        rows=[list(r) for r in base.inline_keyboard]
        if not any('ست' in getattr(b,'text','') or 'Set up' in getattr(b,'text','') for r in rows for b in r):
            rows.insert(2,[InlineKeyboardButton('🤖 ست‌کردن و بررسی ربات' if lang(uid)=='fa' else '🤖 Set up / verify bot',callback_data='v25:fsub:setbot')])
        return InlineKeyboardMarkup(rows)
except Exception:
    pass
'''
    s += patch
    BOT.write_text(s,encoding='utf-8')
    print('quality final patch applied')
else:
    print('already applied')
