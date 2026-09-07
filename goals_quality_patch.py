from pathlib import Path
import re

P=Path('/app/config.py')
s=P.read_text(encoding='utf-8')
MARK='GOALS_LIBRARY_QUALITY_V1'
if MARK not in s:
    fa='''GOALS_FA = {
    "🩺 سلامتی": ["💧 نوشیدن ۸ لیوان آب","🚶 ۳۰ دقیقه پیاده‌روی","🍎 خوردن میوه","🥗 خوردن سبزیجات","😴 خواب ۷ تا ۸ ساعت","🩺 رزرو وقت پزشک","🧪 انجام آزمایش دوره‌ای","🦷 معاینه دندانپزشکی","💊 مصرف داروی تجویزشده طبق برنامه","🧘 ۱۰ دقیقه آرام‌سازی","🌤️ ۱۵ دقیقه هوای آزاد","👀 استراحت به چشم‌ها","🧴 مراقبت از پوست","🧼 شستن دست‌ها در زمان مناسب","🛌 خوابیدن قبل از ساعت مشخص"],
    "🏃 ورزش": ["🏋️ ۳۰ دقیقه ورزش","🚶 ۵۰۰۰ قدم پیاده‌روی","🏃 ۱۰۰۰۰ قدم پیاده‌روی","💪 تمرین شکم","🧘 حرکات کششی","🚴 ۳۰ دقیقه دوچرخه‌سواری","🏊 ۳۰ دقیقه شنا","🏃 ۲۰ دقیقه دویدن","🦵 تمرین پا","💪 تمرین بالاتنه","🧘 یوگا","⚡ ۱۰ دقیقه تمرین سبک","🚶 پیاده‌روی بعد از غذا","🏋️ تمرین قدرتی","🧘 سرد کردن بعد از ورزش"],
    "📚 مطالعه و یادگیری": ["📖 ۳۰ دقیقه مطالعه","📖 ۲۰ دقیقه مطالعه","🔤 یادگیری ۱۰ لغت جدید","📚 مطالعه کتاب","🔁 مرور مطالب","📝 خلاصه‌نویسی یک مطلب","🎓 دیدن یک درس آموزشی","🧠 حل ۱۰ سؤال","💻 یادگیری یک مهارت جدید","🗣️ تمرین زبان","📑 خواندن یک مقاله","✍️ نوشتن یک صفحه یادداشت","🎧 گوش دادن به محتوای آموزشی","🔎 تحقیق درباره یک موضوع","🏁 تمام کردن یک فصل"],
    "💼 کار و شغل": ["📝 برنامه‌ریزی روز","⭐ انجام مهم‌ترین کار روز","🎯 ۳۰ دقیقه کار بدون حواس‌پرتی","📋 بررسی کارهای امروز","📥 مرتب کردن پیام‌ها","📧 پاسخ به ایمیل‌های مهم","📞 انجام یک تماس کاری","🗂 مرتب کردن فایل‌ها","📌 تعیین ۳ اولویت روز","⏱️ یک جلسه تمرکز ۶۰ دقیقه‌ای","🤝 پیگیری یک مشتری","📈 بررسی پیشرفت کار","🧹 خلوت کردن میز کار","🗓 برنامه‌ریزی فردا","🏁 بستن کارهای نیمه‌تمام"],
    "🍎 تغذیه": ["🥚 صبحانه سالم","🥗 ناهار سالم","🍲 شام سبک","🥤 نخوردن نوشابه","🍬 کاهش مصرف شیرینی","💧 نوشیدن آب کافی","🥜 خوردن میان‌وعده سالم","🥦 اضافه کردن سبزی به وعده غذایی","🍊 خوردن یک میوه","🧂 کاهش نمک","🍟 حذف یک غذای ناسالم","🍵 نوشیدنی کم‌شیرین","🍽️ آهسته غذا خوردن","🥣 آماده‌کردن غذای خانگی","📝 ثبت وعده‌های غذایی"],
    "💰 مالی": ["🧾 ثبت هزینه‌های امروز","🏦 بررسی حساب بانکی","💵 پس‌انداز روزانه","✂️ حذف یک هزینه غیرضروری","📊 بررسی بودجه ماهانه","🧮 جمع هزینه‌های هفته","💳 بررسی بدهی‌ها","📅 یادآوری قسط","🏠 یادآوری اجاره","💡 بررسی قبض‌ها","🛒 مقایسه قیمت قبل از خرید","📦 لغو یک خرید غیرضروری","🎯 تعیین هدف پس‌انداز","🧾 نگهداری رسیدها","📈 بررسی درآمد و هزینه"],
    "🏠 خانه": ["🧹 مرتب کردن اتاق","🗂 مرتب کردن میز","🛏 مرتب کردن تخت","🧼 نظافت خانه","👕 مرتب کردن لباس‌ها","🍽 شستن ظرف‌ها","🧺 شستن لباس‌ها","🧹 جارو کردن","🪟 تمیز کردن پنجره","🧴 تمیز کردن سرویس","🗑 بیرون گذاشتن زباله","🧊 مرتب کردن یخچال","📦 مرتب کردن یک کشو","🌱 رسیدگی به گیاهان","🧰 بررسی وسایل خانه"],
    "🧠 تمرکز و رشد شخصی": ["🎯 ۱۰ دقیقه تمرکز","🧘 ۱۰ دقیقه مدیتیشن","📵 ۳۰ دقیقه بدون موبایل","🚫 ۳۰ دقیقه بدون شبکه اجتماعی","🌱 نوشتن یک نکته مثبت","📓 نوشتن برنامه فردا","🙏 ثبت ۳ چیز خوب امروز","🧠 یادگیری یک نکته تازه","⏳ انجام یک کار عقب‌افتاده","💬 گفت‌وگوی خوب با یک دوست","❤️ انجام یک کار برای خودم","🎯 تعیین یک هدف کوچک","🌙 خاموش کردن صفحه‌نمایش قبل خواب","🪞 نوشتن یک موفقیت کوچک","🚀 انجام یک کار خارج از عادت"],
    "🚗 خودرو": ["🛢 بررسی روغن موتور","🛞 بررسی باد لاستیک","💧 بررسی آب رادیاتور","🧽 تمیز کردن خودرو","🛢 تعویض روغن و فیلتر","🛑 بررسی لنت ترمز","🔋 بررسی باتری","💡 بررسی چراغ‌ها","🧯 بررسی کپسول آتش‌نشانی","🧰 بررسی ابزار و جک","❄️ بررسی کولر","🔥 بررسی بخاری","🧼 شست‌وشوی خودرو","📅 ثبت کیلومتر سرویس","🔧 یادآوری سرویس دوره‌ای"],
    "✨ شخصی": ["🪥 مسواک زدن","✨ رسیدگی به ظاهر","⏳ انجام یک کار عقب‌افتاده","💡 یادگیری یک چیز جدید","🚿 دوش گرفتن","🧴 مراقبت شخصی","👔 آماده کردن لباس فردا","📱 مرتب کردن گوشی","📸 مرتب کردن عکس‌ها","🧹 پاک‌سازی فایل‌های اضافی","📚 مرتب کردن وسایل شخصی","🎵 گوش دادن به موسیقی موردعلاقه","☕ زمان آرام برای خودم","☎️ تماس با یک دوست","🎁 انجام یک کار خوشحال‌کننده"],
}
'''
    en='''GOALS_EN = {
    "🩺 Health": ["💧 Drink water","🚶 30 minute walk","🍎 Eat fruit","🥗 Eat vegetables","😴 Sleep 7 to 8 hours","🩺 Book a doctor visit","🧪 Do a routine checkup","🦷 Dental checkup","💊 Follow medication schedule","🧘 10 minutes relaxation","🌤️ Get fresh air","👀 Rest your eyes","🧴 Skin care","🧼 Practice hand hygiene","🛌 Sleep before target time"],
    "🏃 Fitness": ["🏋️ 30 minute workout","🚶 5000 steps","🏃 10000 steps","💪 Abs workout","🧘 Stretching","🚴 30 minute cycling","🏊 30 minute swimming","🏃 20 minute run","🦵 Leg workout","💪 Upper-body workout","🧘 Yoga","⚡ 10 minute light workout","🚶 Walk after a meal","🏋️ Strength training","🧘 Cool down"],
    "📚 Study & Learning": ["📖 Study for 30 minutes","📖 Study for 20 minutes","🔤 Learn 10 new words","📚 Read a book","🔁 Review lessons","📝 Summarize a topic","🎓 Watch one lesson","🧠 Solve 10 questions","💻 Learn a new skill","🗣️ Practice a language","📑 Read an article","✍️ Write one page","🎧 Listen to educational content","🔎 Research a topic","🏁 Finish one chapter"],
    "💼 Work": ["📝 Plan your day","⭐ Do the most important task","🎯 30 minutes focused work","📋 Review today's tasks","📥 Organize messages","📧 Reply to important emails","📞 Make one work call","🗂 Organize files","📌 Set 3 priorities","⏱️ One 60-minute focus session","🤝 Follow up with a customer","📈 Review progress","🧹 Clear your desk","🗓 Plan tomorrow","🏁 Finish pending work"],
    "🍎 Nutrition": ["🥚 Healthy breakfast","🥗 Healthy lunch","🍲 Light dinner","🥤 No soft drinks","🍬 Reduce sweets","💧 Drink enough water","🥜 Healthy snack","🥦 Add vegetables","🍊 Eat one fruit","🧂 Reduce salt","🍟 Skip one unhealthy food","🍵 Low-sugar drink","🍽️ Eat slowly","🥣 Cook at home","📝 Log meals"],
    "💰 Finance": ["🧾 Record today's expenses","🏦 Check your bank account","💵 Save money","✂️ Remove one unnecessary expense","📊 Review monthly budget","🧮 Total weekly expenses","💳 Check debts","📅 Remember an installment","🏠 Remember rent","💡 Check bills","🛒 Compare prices before buying","📦 Skip an unnecessary purchase","🎯 Set a savings goal","🧾 Keep receipts","📈 Review income and expenses"],
    "🏠 Home": ["🧹 Clean your room","🗂 Organize your desk","🛏 Make your bed","🧼 Clean the house","👕 Organize clothes","🍽 Wash dishes","🧺 Do laundry","🧹 Sweep","🪟 Clean a window","🧴 Clean bathroom","🗑 Take out trash","🧊 Organize fridge","📦 Organize one drawer","🌱 Care for plants","🧰 Check home supplies"],
    "🧠 Focus & Growth": ["🎯 10 minutes of focus","🧘 10 minutes meditation","📵 30 minutes without phone","🚫 30 minutes without social media","🌱 Write one positive thought","📓 Plan tomorrow","🙏 Write 3 good things","🧠 Learn one new thing","⏳ Finish a delayed task","💬 Talk with a friend","❤️ Do something for yourself","🎯 Set a small goal","🌙 Screen-free before bed","🪞 Write one small win","🚀 Try something outside routine"],
    "🚗 Car": ["🛢 Check engine oil","🛞 Check tire pressure","💧 Check coolant","🧽 Clean the car","🛢 Change oil and filter","🛑 Check brake pads","🔋 Check battery","💡 Check lights","🧯 Check fire extinguisher","🧰 Check jack and tools","❄️ Check AC","🔥 Check heater","🧼 Wash the car","📅 Record service mileage","🔧 Schedule routine service"],
    "✨ Personal": ["🪥 Brush your teeth","✨ Personal care","⏳ Finish one delayed task","💡 Learn something new","🚿 Take a shower","🧴 Personal grooming","👔 Prepare tomorrow's clothes","📱 Organize your phone","📸 Organize photos","🧹 Remove unused files","📚 Organize personal items","🎵 Listen to favorite music","☕ Take quiet time","☎️ Call a friend","🎁 Do something enjoyable"],
}
'''
    s=re.sub(r'GOALS_FA = \{.*?\n\}\n\n# ── Goal categories \(English\)', fa+'\n# ── Goal categories (English)', s, count=1, flags=re.S)
    s=re.sub(r'GOALS_EN = \{.*?\n\}\n\n# ── Translations', en+'\n# ── Translations', s, count=1, flags=re.S)
    s += '\n# '+MARK+'\n'
    P.write_text(s,encoding='utf-8')
    print('expanded prepared goals')
else:
    print('already applied')
