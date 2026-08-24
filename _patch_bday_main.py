"""One-shot: register birthday handlers in main()."""
src = open("bot.py", encoding="utf-8").read()

a = '    app.add_handler(CommandHandler("support", support_start))\n'
assert src.count(a) == 1
src = src.replace(a, a + '    app.add_handler(CommandHandler("birthday", birthday_command))\n')

b = '    app.add_handler(CallbackQueryHandler(settings_callback, pattern=r"^settings:"))\n'
assert src.count(b) == 1
src = src.replace(b, b + '    app.add_handler(CallbackQueryHandler(birthday_callback, pattern=r"^bd:"))\n')

c = '        app.job_queue.run_repeating(weekly_owner_backup_job, interval=3600, first=120)\n'
assert src.count(c) == 1
src = src.replace(c, c + '        app.job_queue.run_repeating(birthday_occasion_job, interval=60, first=65)\n')

open("bot.py", "w", encoding="utf-8").write(src)
print("registrations OK")
