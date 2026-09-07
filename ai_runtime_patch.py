from pathlib import Path

BOT=Path("/app/bot.py")
s=BOT.read_text(encoding="utf-8")
if "# AI_SQLITE_PATCH_V1" in s:
    raise SystemExit(0)
start=s.index("async def ai_chat_text(update,context):")
end=s.index("\n\nasync def build_daily_report():", start)
new_function='''async def ai_chat_text(update,context):
    if not context.user_data.get("ai_chat"):
        return False
    uid=update.effective_user.id
    text=update.message.text.strip()
    if text in ("⬅️ برگشت","⬅️ Back","🏠 منوی اصلی","🏠 Main Menu"):
        clear_flow(context)
        await update.message.reply_text("🏠 منوی اصلی",reply_markup=keyboard(uid))
        return True

    api_key=os.environ.get("OPENAI_API_KEY","").strip()
    if not api_key and not omniroute_configured() and not n8n_configured() and not GEMINI_API_KEY:
        clear_flow(context)
        await update.message.reply_text(
            "⚠️ در حال حاضر دستیار هوشمند موقتاً در دسترس نیست.\\n"
            "لطفاً کمی بعد دوباره تلاش بفرمایید. 🌷",
            reply_markup=keyboard(uid),
        )
        return True

    today=datetime.now(TZ).date().isoformat()
    limit=100 if is_vip(uid) else 10

    def _quota_read():
        last_error=None
        for attempt in range(5):
            c=None
            try:
                c=db()
                c.execute("INSERT OR IGNORE INTO user_settings(user_id) VALUES(?)",(uid,))
                r=c.execute(
                    "SELECT ai_daily_used,ai_used_date FROM user_settings WHERE user_id=?",
                    (uid,),
                ).fetchone()
                return r
            except sqlite3.OperationalError as exc:
                last_error=exc
                if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                    raise
                time.sleep(0.15 * (attempt + 1))
            finally:
                if c is not None:
                    try:
                        c.rollback()
                    except Exception:
                        pass
                    try:
                        c.close()
                    except Exception:
                        pass
        raise last_error

    try:
        row=_quota_read()
        used=row["ai_daily_used"] if row and row["ai_used_date"]==today else 0
    except sqlite3.OperationalError as exc:
        logger.exception("AI quota read failed: %s", exc)
        await update.message.reply_text(
            "⚠️ دیتابیس موقتاً مشغول است. دوباره تلاش کن." if lang(uid)=="fa" else
            "⚠️ The database is temporarily busy. Please try again.",
            reply_markup=nav_keyboard(uid),
        )
        return True

    if used>=limit:
        await update.message.reply_text(
            "⛔ سهمیه AI امروز تمام شده است." if lang(uid)=="fa" else
            "⛔ Your AI quota for today is used up.",
            reply_markup=nav_keyboard(uid),
        )
        return True

    try:
        prompt=(
            "پاسخ کوتاه، مفید، مودبانه و امن به این سوال کاربر بده. "
            "اگر موضوع پزشکی یا مالی است، پاسخ عمومی و غیرقطعی نگه دار.\\n\\n" + text
        )
        answer=ai_text_generate(prompt, max_output_tokens=500, purpose="chat")
        if not answer:
            raise RuntimeError("No AI provider returned a response")

        quota_saved=False
        last_quota_error=None
        for attempt in range(5):
            c=None
            try:
                c=db()
                c.execute("INSERT OR IGNORE INTO user_settings(user_id) VALUES(?)",(uid,))
                c.execute(
                    "UPDATE user_settings SET ai_daily_used=?,ai_used_date=? WHERE user_id=?",
                    (used+1,today,uid),
                )
                c.commit()
                quota_saved=True
                break
            except sqlite3.OperationalError as exc:
                last_quota_error=exc
                try:
                    if c is not None:
                        c.rollback()
                except Exception:
                    pass
                if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                    break
                time.sleep(0.15 * (attempt + 1))
            finally:
                if c is not None:
                    try:
                        c.close()
                    except Exception:
                        pass
        if not quota_saved:
            logger.error("AI quota update failed after successful AI response: %s", last_quota_error)

        await update.message.reply_text(answer,reply_markup=nav_keyboard(uid))
    except Exception as e:
        logger.exception("AI chat failed: %s",e)
        clear_flow(context)
        await update.message.reply_text(
            (
                "⚠️ متأسفانه در حال حاضر پاسخ هوش مصنوعی دریافت نشد. "
                "لطفاً کمی بعد دوباره تلاش بفرمایید."
            )
            if lang(uid) == "fa" else
            (
                "⚠️ The AI provider did not return a response right now. "
                "Please try again in a little while."
            ),
            reply_markup=keyboard(uid)
        )
    return True
'''
s=s[:start]+new_function+s[end:]
BOT.write_text(s,encoding="utf-8")
print("AI SQLite reliability patch applied")
