import logging
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, filters, ContextTypes
)

# ── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN    = "8700379788:AAFmgUnnFoSY0XFFAiZlmW_GQVuKE7nCy-Q"
SUPA_URL     = "https://etwjakuffkrqlypvahuq.supabase.co/rest/v1"
SUPA_KEY     = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImV0d2pha3VmZmtycWx5cHZhaHVxIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NTQ0OTA3MywiZXhwIjoyMDkxMDI1MDczfQ.ElJnwlD3yApdXQBlfq0SmQfSd7I3bGzZ3O1kOD27428"
HEADERS      = {"apikey": SUPA_KEY, "Authorization": f"Bearer {SUPA_KEY}", "Content-Type": "application/json", "Prefer": "return=representation"}

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

# ── Conversation states ───────────────────────────────────────────────────────
ENTER_ID, ENTER_NAME, SELECT_SEMESTER, SELECT_SUBJECTS = range(4)

TIME_LABELS = {"8-10": "8:00-10:00", "10-12": "10:00-12:00", "12-2": "12:00-14:00"}

# ── Supabase helpers (plain HTTP) ─────────────────────────────────────────────

def db_get(table, params):
    r = requests.get(f"{SUPA_URL}/{table}", headers=HEADERS, params=params)
    return r.json() if r.ok else []

def db_post(table, data):
    r = requests.post(f"{SUPA_URL}/{table}", headers=HEADERS, json=data)
    return r.json() if r.ok else None

def db_patch(table, match_params, data):
    r = requests.patch(f"{SUPA_URL}/{table}", headers=HEADERS, params=match_params, json=data)
    return r.ok

def get_student_by_telegram(telegram_id):
    res = db_get("students", {"telegram_id": f"eq.{telegram_id}", "limit": 1})
    return res[0] if res else None

def get_student_by_user_id(user_id):
    res = db_get("students", {"user_id": f"eq.{user_id}", "limit": 1})
    return res[0] if res else None

def get_subjects_by_semester(semester):
    return db_get("subjects", {"semester": f"eq.{semester}", "order": "code"})

def get_subjects_by_codes(codes):
    if not codes:
        return []
    return db_get("subjects", {"code": f"in.({','.join(codes)})"})

def get_timetable(subject_codes):
    if not subject_codes:
        return []
    return db_get("timetable_slots", {"subject_code": f"in.({','.join(subject_codes)})", "select": "*, subjects(title)"})

def format_timetable(slots):
    if not slots:
        return "❌ No timetable slots assigned yet. Check back later."
    by_day = {}
    for slot in slots:
        day = slot["day"]
        by_day.setdefault(day, []).append(slot)
    lines = ["📅 *Your Timetable*\n"]
    for day in ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday"]:
        if day not in by_day:
            continue
        lines.append(f"*{day}*")
        for s in sorted(by_day[day], key=lambda x: x["time_slot"]):
            subj = s.get("subjects") or {}
            title = subj.get("title", s["subject_code"]) if isinstance(subj, dict) else s["subject_code"]
            time  = TIME_LABELS.get(s["time_slot"], s["time_slot"])
            room  = f" | Room {s['room']}" if s.get("room") else ""
            lines.append(f"  🕐 {time} — {title}{room}")
        lines.append("")
    return "\n".join(lines)

# ── /start ────────────────────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    student = get_student_by_telegram(update.effective_user.id)
    if student and student.get("setup_done"):
        await show_main_menu(update, ctx, student)
        return ConversationHandler.END
    await update.message.reply_text(
        "👋 Welcome to the *English Department Portal*!\n\nEnter your *Student ID* to register or log in:",
        parse_mode="Markdown"
    )
    return ENTER_ID

async def enter_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.text.strip()
    student = get_student_by_user_id(user_id)
    if student:
        db_patch("students", {"user_id": f"eq.{user_id}"}, {"telegram_id": update.effective_user.id})
        student["telegram_id"] = update.effective_user.id
        if student.get("setup_done"):
            await show_main_menu(update, ctx, student)
            return ConversationHandler.END
        ctx.user_data["student"] = student
        await update.message.reply_text("✅ Account found! Enter your *display name*:", parse_mode="Markdown")
        return ENTER_NAME
    else:
        ctx.user_data["new_user_id"] = user_id
        await update.message.reply_text(
            f"📝 ID *{user_id}* not found. Creating new account.\n\nEnter your *full name*:",
            parse_mode="Markdown"
        )
        return ENTER_NAME

async def enter_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("Please enter a valid name.")
        return ENTER_NAME
    ctx.user_data["display_name"] = name
    if "new_user_id" in ctx.user_data:
        user_id = ctx.user_data["new_user_id"]
        res = db_post("students", {
            "user_id": user_id, "display_name": name, "password": user_id,
            "telegram_id": update.effective_user.id, "subjects": [], "setup_done": False
        })
        ctx.user_data["student"] = res[0] if isinstance(res, list) else res or {"user_id": user_id}
    else:
        student = ctx.user_data["student"]
        db_patch("students", {"user_id": f"eq.{student['user_id']}"}, {"display_name": name})
        ctx.user_data["student"]["display_name"] = name

    keyboard = [[InlineKeyboardButton(f"Semester {i}", callback_data=f"sem_{i}")] for i in range(1, 9)]
    await update.message.reply_text("📚 Which *semester* are you in?",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return SELECT_SEMESTER

async def select_semester(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    semester = int(query.data.split("_")[1])
    ctx.user_data["semester"] = semester
    ctx.user_data["selected_subjects"] = []
    subjects = get_subjects_by_semester(semester)
    ctx.user_data["subjects_list"] = subjects
    await show_subject_picker(query, ctx, subjects, semester)
    return SELECT_SUBJECTS

async def show_subject_picker(query, ctx, subjects, semester):
    selected = ctx.user_data.get("selected_subjects", [])
    keyboard = []
    for s in subjects:
        check = "✅ " if s["code"] in selected else ""
        keyboard.append([InlineKeyboardButton(f"{check}{s['title']}", callback_data=f"subj_{s['code']}")])
    keyboard.append([InlineKeyboardButton("✔️ Done — Save Subjects", callback_data="subjects_done")])
    text = f"📖 *Semester {semester} Subjects*\nTap to select your enrolled subjects:"
    try:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    except Exception:
        await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def toggle_subject(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    code = query.data.split("_", 1)[1]
    selected = ctx.user_data.get("selected_subjects", [])
    if code in selected:
        selected.remove(code)
    else:
        selected.append(code)
    ctx.user_data["selected_subjects"] = selected
    await show_subject_picker(query, ctx, ctx.user_data["subjects_list"], ctx.user_data["semester"])
    return SELECT_SUBJECTS

async def subjects_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    selected = ctx.user_data.get("selected_subjects", [])
    if not selected:
        await query.answer("Please select at least one subject!", show_alert=True)
        return SELECT_SUBJECTS
    student = ctx.user_data["student"]
    db_patch("students", {"user_id": f"eq.{student['user_id']}"}, {
        "subjects": selected, "semester": ctx.user_data["semester"], "setup_done": True
    })
    student.update({"subjects": selected, "setup_done": True, "semester": ctx.user_data["semester"]})
    await query.edit_message_text(
        f"✅ *Registration complete!*\n\nWelcome, *{student.get('display_name', student['user_id'])}*!\n"
        f"You selected *{len(selected)}* subject(s).", parse_mode="Markdown"
    )
    await show_main_menu(update, ctx, student)
    return ConversationHandler.END

# ── Main menu ─────────────────────────────────────────────────────────────────

async def show_main_menu(update, ctx, student):
    name = student.get("display_name") or student.get("user_id", "Student")
    keyboard = [
        [InlineKeyboardButton("📅 My Timetable",  callback_data="menu_timetable")],
        [InlineKeyboardButton("📚 My Subjects",    callback_data="menu_subjects")],
        [InlineKeyboardButton("➕ Add Subjects",   callback_data="menu_add")],
        [InlineKeyboardButton("➖ Drop a Subject", callback_data="menu_drop")],
    ]
    msg = update.callback_query.message if update.callback_query else update.message
    await msg.reply_text(
        f"🏛 *English Department Portal*\nHello, *{name}*! What would you like to do?",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
    )

async def menu_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    student = get_student_by_telegram(update.effective_user.id)
    if not student:
        await query.message.reply_text("Use /start to register first.")
        return
    action = query.data

    if action == "menu_timetable":
        slots = get_timetable(student.get("subjects") or [])
        await query.message.reply_text(format_timetable(slots), parse_mode="Markdown")

    elif action == "menu_subjects":
        codes = student.get("subjects") or []
        if not codes:
            await query.message.reply_text("You have no subjects. Use /start to set up.")
            return
        all_subs = get_subjects_by_codes(codes)
        by_sem = {}
        for s in all_subs:
            by_sem.setdefault(s["semester"], []).append(s["title"])
        lines = ["📚 *Your Subjects*\n"]
        for sem in sorted(by_sem):
            lines.append(f"*Semester {sem}*")
            for t in by_sem[sem]:
                lines.append(f"  • {t}")
            lines.append("")
        await query.message.reply_text("\n".join(lines), parse_mode="Markdown")

    elif action == "menu_add":
        ctx.user_data["student"] = student
        keyboard = [[InlineKeyboardButton(f"Semester {i}", callback_data=f"addsem_{i}")] for i in range(1, 9)]
        await query.message.reply_text("Which semester do you want to add subjects from?",
            reply_markup=InlineKeyboardMarkup(keyboard))

    elif action == "menu_drop":
        codes = student.get("subjects") or []
        if not codes:
            await query.message.reply_text("You have no subjects to drop.")
            return
        all_subs = get_subjects_by_codes(codes)
        keyboard = [[InlineKeyboardButton(f"❌ {s['title']}", callback_data=f"drop_{s['code']}")] for s in all_subs]
        keyboard.append([InlineKeyboardButton("↩️ Cancel", callback_data="menu_cancel")])
        await query.message.reply_text("Select a subject to drop:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif action == "menu_cancel":
        await show_main_menu(update, ctx, student)

async def add_semester_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    semester = int(query.data.split("_")[1])
    student = ctx.user_data.get("student") or get_student_by_telegram(update.effective_user.id)
    ctx.user_data["student"] = student
    subjects = get_subjects_by_semester(semester)
    current = list(student.get("subjects") or [])
    ctx.user_data["selected_subjects"] = current
    ctx.user_data["subjects_list"] = subjects
    ctx.user_data["semester"] = semester
    keyboard = []
    for s in subjects:
        check = "✅ " if s["code"] in current else ""
        keyboard.append([InlineKeyboardButton(f"{check}{s['title']}", callback_data=f"addsubj_{s['code']}")])
    keyboard.append([InlineKeyboardButton("✔️ Save", callback_data="adddone")])
    await query.edit_message_text(f"📖 *Semester {semester}* — tap to add/remove:",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def add_subject_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    code = query.data.split("_", 1)[1]
    selected = ctx.user_data.get("selected_subjects", [])
    if code in selected:
        selected.remove(code)
    else:
        selected.append(code)
    ctx.user_data["selected_subjects"] = selected
    subjects = ctx.user_data["subjects_list"]
    keyboard = []
    for s in subjects:
        check = "✅ " if s["code"] in selected else ""
        keyboard.append([InlineKeyboardButton(f"{check}{s['title']}", callback_data=f"addsubj_{s['code']}")])
    keyboard.append([InlineKeyboardButton("✔️ Save", callback_data="adddone")])
    try:
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception:
        pass

async def add_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    selected = ctx.user_data.get("selected_subjects", [])
    student = ctx.user_data.get("student") or get_student_by_telegram(update.effective_user.id)
    db_patch("students", {"user_id": f"eq.{student['user_id']}"}, {"subjects": selected})
    await query.edit_message_text(f"✅ Subjects updated! You now have *{len(selected)}* subject(s).", parse_mode="Markdown")
    student["subjects"] = selected
    await show_main_menu(update, ctx, student)

async def drop_subject(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    code = query.data.split("_", 1)[1]
    student = get_student_by_telegram(update.effective_user.id)
    if not student:
        return
    current = list(student.get("subjects") or [])
    if code in current:
        current.remove(code)
    db_patch("students", {"user_id": f"eq.{student['user_id']}"}, {"subjects": current})
    subs = get_subjects_by_codes([code])
    title = subs[0]["title"] if subs else code
    await query.edit_message_text(f"✅ Dropped *{title}* from your subjects.", parse_mode="Markdown")
    student["subjects"] = current
    await show_main_menu(update, ctx, student)

# ── Shortcut commands ─────────────────────────────────────────────────────────

async def timetable_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    student = get_student_by_telegram(update.effective_user.id)
    if not student:
        await update.message.reply_text("Use /start to register first.")
        return
    slots = get_timetable(student.get("subjects") or [])
    await update.message.reply_text(format_timetable(slots), parse_mode="Markdown")

async def mysubjects_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    student = get_student_by_telegram(update.effective_user.id)
    if not student:
        await update.message.reply_text("Use /start to register first.")
        return
    codes = student.get("subjects") or []
    if not codes:
        await update.message.reply_text("No subjects selected. Use /start to set up.")
        return
    all_subs = get_subjects_by_codes(codes)
    by_sem = {}
    for s in all_subs:
        by_sem.setdefault(s["semester"], []).append(s["title"])
    lines = ["📚 *Your Subjects*\n"]
    for sem in sorted(by_sem):
        lines.append(f"*Semester {sem}*")
        for t in by_sem[sem]:
            lines.append(f"  • {t}")
        lines.append("")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def menu_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    student = get_student_by_telegram(update.effective_user.id)
    if not student or not student.get("setup_done"):
        await update.message.reply_text("Use /start to register first.")
        return
    await show_main_menu(update, ctx, student)

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ENTER_ID:        [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_id)],
            ENTER_NAME:      [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_name)],
            SELECT_SEMESTER: [CallbackQueryHandler(select_semester, pattern="^sem_")],
            SELECT_SUBJECTS: [
                CallbackQueryHandler(toggle_subject, pattern="^subj_"),
                CallbackQueryHandler(subjects_done,  pattern="^subjects_done$"),
            ],
        },
        fallbacks=[CommandHandler("start", start)],
    )
    app.add_handler(conv)
    app.add_handler(CommandHandler("timetable",  timetable_cmd))
    app.add_handler(CommandHandler("mysubjects", mysubjects_cmd))
    app.add_handler(CommandHandler("menu",       menu_cmd))
    app.add_handler(CallbackQueryHandler(menu_handler,       pattern="^menu_"))
    app.add_handler(CallbackQueryHandler(add_semester_handler, pattern="^addsem_"))
    app.add_handler(CallbackQueryHandler(add_subject_toggle,   pattern="^addsubj_"))
    app.add_handler(CallbackQueryHandler(add_done,             pattern="^adddone$"))
    app.add_handler(CallbackQueryHandler(drop_subject,         pattern="^drop_"))
    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
