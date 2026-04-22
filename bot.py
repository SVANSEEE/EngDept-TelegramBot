import logging
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, filters, ContextTypes
)

BOT_TOKEN = "8700379788:AAFmgUnnFoSY0XFFAiZlmW_GQVuKE7nCy-Q"
SUPA_URL  = "https://etwjakuffkrqlypvahuq.supabase.co/rest/v1"
SUPA_KEY  = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImV0d2pha3VmZmtycWx5cHZhaHVxIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NTQ0OTA3MywiZXhwIjoyMDkxMDI1MDczfQ.ElJnwlD3yApdXQBlfq0SmQfSd7I3bGzZ3O1kOD27428"
HEADERS   = {
    "apikey": SUPA_KEY,
    "Authorization": f"Bearer {SUPA_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# conversation states
(ENTER_ID, ENTER_NAME, SELECT_SEMESTER, SELECT_SUBJECTS,
 ADD_SEMESTER, ADD_SUBJECTS, DROP_SUBJECT, WAITING_FEEDBACK) = range(8)

TIME_LABELS = {"8-10": "8:00-10:00", "10-12": "10:00-12:00", "12-2": "12:00-14:00"}

# ── DB helpers ────────────────────────────────────────────────────────────────

def db_get(table, params):
    try:
        r = requests.get(f"{SUPA_URL}/{table}", headers=HEADERS, params=params, timeout=10)
        return r.json() if r.ok else []
    except Exception as e:
        logger.error(f"db_get {table}: {e}")
        return []

def db_post(table, data):
    try:
        r = requests.post(f"{SUPA_URL}/{table}", headers=HEADERS, json=data, timeout=10)
        return r.json() if r.ok else None
    except Exception as e:
        logger.error(f"db_post {table}: {e}")
        return None

def db_patch(table, match_params, data):
    try:
        r = requests.patch(f"{SUPA_URL}/{table}", headers=HEADERS, params=match_params, json=data, timeout=10)
        return r.ok
    except Exception as e:
        logger.error(f"db_patch {table}: {e}")
        return False

def get_student_by_telegram(tid):
    # try as integer first, fallback to string
    res = db_get("students", {"telegram_id": f"eq.{int(tid)}", "limit": 1})
    if not res:
        res = db_get("students", {"telegram_id": f"eq.{str(tid)}", "limit": 1})
    return res[0] if res else None

def get_student_by_user_id(uid):
    res = db_get("students", {"user_id": f"eq.{uid}", "limit": 1})
    return res[0] if res else None

def get_subjects_by_semester(sem):
    return db_get("subjects", {"semester": f"eq.{sem}", "order": "code"})

def get_subjects_by_codes(codes):
    if not codes:
        return []
    return db_get("subjects", {"code": f"in.({','.join(codes)})", "order": "semester,code"})

def get_timetable(codes):
    if not codes:
        return []
    return db_get("timetable_slots", {"subject_code": f"in.({','.join(codes)})", "select": "*, subjects(title)"})

def is_valid_student_id(uid):
    return uid.isdigit() and len(uid) == 5 and (uid.startswith("64") or uid.startswith("65"))

def format_timetable(slots):
    if not slots:
        return "No timetable slots assigned yet. The admin will add them soon."
    by_day = {}
    for s in slots:
        by_day.setdefault(s["day"], []).append(s)
    lines = ["--- Your Timetable ---\n"]
    for day in ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday"]:
        if day not in by_day:
            continue
        lines.append(f"{day}:")
        for s in sorted(by_day[day], key=lambda x: x["time_slot"]):
            subj = s.get("subjects") or {}
            title = subj.get("title", s["subject_code"]) if isinstance(subj, dict) else s["subject_code"]
            time  = TIME_LABELS.get(s["time_slot"], s["time_slot"])
            room  = f" | Room {s['room']}" if s.get("room") else ""
            lines.append(f"  {time}  {title}{room}")
        lines.append("")
    return "\n".join(lines)

# ── Keyboards ─────────────────────────────────────────────────────────────────

def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("My Timetable",  callback_data="menu_timetable"),
         InlineKeyboardButton("My Subjects",   callback_data="menu_subjects")],
        [InlineKeyboardButton("Add Subjects",  callback_data="menu_add"),
         InlineKeyboardButton("Drop Subject",  callback_data="menu_drop")],
        [InlineKeyboardButton("My Profile",    callback_data="menu_profile")],
        [InlineKeyboardButton("Send Feedback", callback_data="menu_feedback")],
    ])

def semester_keyboard(prefix):
    rows = []
    row = []
    for i in range(1, 9):
        row.append(InlineKeyboardButton(f"Sem {i}", callback_data=f"{prefix}{i}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)

def subject_keyboard(subjects, selected, cb_prefix, done_cb):
    keyboard = []
    for s in subjects:
        mark = "[+] " if s["code"] in selected else "[ ] "
        keyboard.append([InlineKeyboardButton(f"{mark}{s['title']}", callback_data=f"{cb_prefix}{s['code']}")])
    keyboard.append([InlineKeyboardButton("Done - Save", callback_data=done_cb)])
    return InlineKeyboardMarkup(keyboard)

async def show_main_menu(update, ctx, student):
    name = student.get("display_name") or student.get("user_id", "Student")
    msg = update.callback_query.message if update.callback_query else update.message
    await msg.reply_text(
        f"English Department Portal\nHello, {name}!\nWhat would you like to do?",
        reply_markup=main_menu_keyboard()
    )

# ── /start registration flow ──────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        ctx.user_data.clear()
        student = get_student_by_telegram(update.effective_user.id)
        if student and student.get("setup_done"):
            await show_main_menu(update, ctx, student)
            return ConversationHandler.END
        await update.message.reply_text(
            "Welcome to the English Department Portal!\n\n"
            "Enter your 5-digit Student ID (starts with 64 or 65):"
        )
        return ENTER_ID
    except Exception as e:
        logger.error(f"start: {e}")
        await update.message.reply_text("Error. Try /start again.")
        return ConversationHandler.END

async def enter_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        uid = update.message.text.strip()

        if not is_valid_student_id(uid):
            await update.message.reply_text(
                "Invalid Student ID.\n"
                "Must be exactly 5 digits and start with 64 or 65.\n"
                "Example: 64123 or 65456\n\nTry again:"
            )
            return ENTER_ID

        student = get_student_by_user_id(uid)
        if student:
            db_patch("students", {"user_id": f"eq.{uid}"}, {"telegram_id": update.effective_user.id})
            student["telegram_id"] = update.effective_user.id
            if student.get("setup_done"):
                await show_main_menu(update, ctx, student)
                return ConversationHandler.END
            ctx.user_data["student"] = student
            await update.message.reply_text("Account found! Enter your display name:")
            return ENTER_NAME
        else:
            db_post("logs", {
                "type": "unregistered_attempt",
                "user_id": str(update.effective_user.id),
                "action": f"@{update.effective_user.username or update.effective_user.id} tried ID: {uid}"
            })
            ctx.user_data["new_user_id"] = uid
            await update.message.reply_text(
                f"Student ID {uid} is not in the system yet.\n\n"
                "You can still register and your account will be saved.\n"
                "The admin can verify you later.\n\n"
                "Enter your full name to continue:",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("Try a different ID", callback_data="try_again")
                ]])
            )
            return ENTER_NAME
    except Exception as e:
        logger.error(f"enter_id: {e}")
        await update.message.reply_text("Error. Try again:")
        return ENTER_ID

async def try_again(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data.clear()
    await query.edit_message_text("Enter your 5-digit Student ID (starts with 64 or 65):")
    return ENTER_ID

async def enter_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        name = update.message.text.strip()
        if not name:
            await update.message.reply_text("Please enter a valid name.")
            return ENTER_NAME
        ctx.user_data["display_name"] = name

        if "new_user_id" in ctx.user_data:
            uid = ctx.user_data["new_user_id"]
            res = db_post("students", {
                "user_id": uid, "display_name": name, "password": uid,
                "telegram_id": update.effective_user.id, "subjects": [], "setup_done": False
            })
            ctx.user_data["student"] = (res[0] if isinstance(res, list) else res) or {"user_id": uid, "display_name": name}
        else:
            student = ctx.user_data["student"]
            db_patch("students", {"user_id": f"eq.{student['user_id']}"}, {"display_name": name})
            ctx.user_data["student"]["display_name"] = name

        await update.message.reply_text(
            f"Nice to meet you, {name}!\n\nWhich semester are you in?",
            reply_markup=semester_keyboard("sem_")
        )
        return SELECT_SEMESTER
    except Exception as e:
        logger.error(f"enter_name: {e}")
        await update.message.reply_text("Error. Try /start again.")
        return ConversationHandler.END

async def select_semester(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        sem = int(query.data.split("_")[1])
        ctx.user_data["semester"] = sem
        ctx.user_data["reg_selected"] = []
        subjects = get_subjects_by_semester(sem)
        ctx.user_data["reg_subjects"] = subjects
        await query.edit_message_text(
            f"Semester {sem} subjects.\nTap to select the ones you are enrolled in:",
            reply_markup=subject_keyboard(subjects, [], "reg_", "reg_done")
        )
        return SELECT_SUBJECTS
    except Exception as e:
        logger.error(f"select_semester: {e}")
        return ConversationHandler.END

async def reg_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        code = query.data[4:]  # strip "reg_"
        selected = ctx.user_data.get("reg_selected", [])
        if code in selected:
            selected.remove(code)
        else:
            selected.append(code)
        ctx.user_data["reg_selected"] = selected
        sem = ctx.user_data["semester"]
        subjects = ctx.user_data["reg_subjects"]
        await query.edit_message_reply_markup(
            reply_markup=subject_keyboard(subjects, selected, "reg_", "reg_done")
        )
        return SELECT_SUBJECTS
    except Exception as e:
        logger.error(f"reg_toggle: {e}")
        return SELECT_SUBJECTS

async def reg_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        selected = ctx.user_data.get("reg_selected", [])
        if not selected:
            await query.answer("Select at least one subject!", show_alert=True)
            return SELECT_SUBJECTS
        student = ctx.user_data["student"]
        sem = ctx.user_data["semester"]
        db_patch("students", {"user_id": f"eq.{student['user_id']}"}, {
            "subjects": selected, "semester": sem, "setup_done": True
        })
        student.update({"subjects": selected, "semester": sem, "setup_done": True})
        await query.edit_message_text(
            f"Registration complete!\n\n"
            f"Welcome, {student.get('display_name', student['user_id'])}!\n"
            f"Semester: {sem}\n"
            f"Subjects enrolled: {len(selected)}\n\n"
            f"Use /menu anytime."
        )
        await show_main_menu(update, ctx, student)
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"reg_done: {e}")
        return ConversationHandler.END

# ── Menu ──────────────────────────────────────────────────────────────────────

async def menu_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        student = get_student_by_telegram(update.effective_user.id)
        if not student or not student.get("setup_done"):
            await update.message.reply_text("Use /start to register first.")
            return
        await show_main_menu(update, ctx, student)
    except Exception as e:
        logger.error(f"menu_cmd: {e}")

async def menu_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        student = get_student_by_telegram(update.effective_user.id)
        if not student:
            await query.message.reply_text("Use /start to register first.")
            return
        action = query.data

        if action == "menu_timetable":
            slots = get_timetable(student.get("subjects") or [])
            await query.message.reply_text(format_timetable(slots))

        elif action == "menu_subjects":
            codes = student.get("subjects") or []
            if not codes:
                await query.message.reply_text("No subjects selected yet. Use Add Subjects.")
                return
            subs = get_subjects_by_codes(codes)
            by_sem = {}
            for s in subs:
                by_sem.setdefault(s["semester"], []).append(s["title"])
            lines = ["--- Your Subjects ---\n"]
            for sem in sorted(by_sem):
                lines.append(f"Semester {sem}:")
                for t in by_sem[sem]:
                    lines.append(f"  - {t}")
                lines.append("")
            await query.message.reply_text("\n".join(lines))

        elif action == "menu_profile":
            await query.message.reply_text(
                f"--- Your Profile ---\n\n"
                f"Name:     {student.get('display_name', '?')}\n"
                f"ID:       {student.get('user_id', '?')}\n"
                f"Semester: {student.get('semester', '?')}\n"
                f"Subjects: {len(student.get('subjects') or [])} enrolled"
            )

        elif action == "menu_add":
            ctx.user_data["add_student"] = student
            await query.message.reply_text(
                "Which semester to add subjects from?",
                reply_markup=semester_keyboard("addsem_")
            )

        elif action == "menu_drop":
            codes = student.get("subjects") or []
            if not codes:
                await query.message.reply_text("You have no subjects to drop.")
                return
            subs = get_subjects_by_codes(codes)
            ctx.user_data["drop_student"] = student
            keyboard = [[InlineKeyboardButton(f"Remove: {s['title']}", callback_data=f"drop_{s['code']}")] for s in subs]
            keyboard.append([InlineKeyboardButton("Cancel", callback_data="drop_cancel")])
            await query.message.reply_text("Select a subject to drop:", reply_markup=InlineKeyboardMarkup(keyboard))

        elif action == "menu_feedback":
            ctx.user_data["waiting_feedback"] = True
            ctx.user_data["feedback_student"] = student
            await query.message.reply_text(
                "Type your message or feedback.\nIt will be saved for the admin.\n\n/cancel to cancel."
            )

    except Exception as e:
        logger.error(f"menu_handler: {e}")
        await query.message.reply_text("Something went wrong. Try again.")

# ── Add subjects (outside conversation) ──────────────────────────────────────

async def addsem_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        sem = int(query.data.split("_")[1])
        # always re-fetch student to get fresh data
        student = get_student_by_telegram(update.effective_user.id)
        if not student:
            await query.message.reply_text("Use /start to register first.")
            return
        ctx.user_data["add_student"] = student
        subjects = get_subjects_by_semester(sem)
        current = list(student.get("subjects") or [])
        ctx.user_data["add_subjects"] = subjects
        ctx.user_data["add_selected"] = current
        ctx.user_data["add_sem"] = sem
        await query.edit_message_text(
            f"Semester {sem} — tap to add/remove.\nCurrently selected shown with [+]:",
            reply_markup=subject_keyboard(subjects, current, "addsubj_", "adddone")
        )
    except Exception as e:
        logger.error(f"addsem_handler: {e}")

async def addsubj_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        code = query.data[8:]  # strip "addsubj_"
        selected = ctx.user_data.get("add_selected", [])
        if code in selected:
            selected.remove(code)
        else:
            selected.append(code)
        ctx.user_data["add_selected"] = selected
        subjects = ctx.user_data["add_subjects"]
        await query.edit_message_reply_markup(
            reply_markup=subject_keyboard(subjects, selected, "addsubj_", "adddone")
        )
    except Exception as e:
        logger.error(f"addsubj_toggle: {e}")

async def adddone_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        selected = ctx.user_data.get("add_selected", [])
        student = get_student_by_telegram(update.effective_user.id)
        if not student:
            return
        db_patch("students", {"user_id": f"eq.{student['user_id']}"}, {"subjects": selected})
        await query.edit_message_text(f"Saved! You now have {len(selected)} subject(s) enrolled.")
        student["subjects"] = selected
        await show_main_menu(update, ctx, student)
    except Exception as e:
        logger.error(f"adddone_handler: {e}")

# ── Drop subject ──────────────────────────────────────────────────────────────

async def drop_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()

        if query.data == "drop_cancel":
            student = get_student_by_telegram(update.effective_user.id)
            await query.edit_message_text("Cancelled.")
            if student:
                await show_main_menu(update, ctx, student)
            return

        code = query.data[5:]  # strip "drop_"
        student = get_student_by_telegram(update.effective_user.id)
        if not student:
            return
        current = list(student.get("subjects") or [])
        if code in current:
            current.remove(code)
        db_patch("students", {"user_id": f"eq.{student['user_id']}"}, {"subjects": current})
        subs = get_subjects_by_codes([code])
        title = subs[0]["title"] if subs else code
        await query.edit_message_text(f"Removed '{title}' from your subjects.")
        student["subjects"] = current
        await show_main_menu(update, ctx, student)
    except Exception as e:
        logger.error(f"drop_handler: {e}")

# ── Feedback & general messages ───────────────────────────────────────────────

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if ctx.user_data.get("waiting_feedback"):
            ctx.user_data["waiting_feedback"] = False
            student = ctx.user_data.get("feedback_student") or get_student_by_telegram(update.effective_user.id)
            uid = student["user_id"] if student else str(update.effective_user.id)
            db_post("logs", {"type": "feedback", "user_id": uid, "action": f"Feedback: {update.message.text}"})
            await update.message.reply_text("Message saved! The admin will review it.\n\nUse /menu to go back.")
            return
        await update.message.reply_text("Use /menu to open the portal or /start to register.")
    except Exception as e:
        logger.error(f"handle_message: {e}")

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("Cancelled. Use /menu.")
    return ConversationHandler.END

# ── Other commands ────────────────────────────────────────────────────────────

async def timetable_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        student = get_student_by_telegram(update.effective_user.id)
        if not student:
            await update.message.reply_text("Use /start to register first.")
            return
        await update.message.reply_text(format_timetable(get_timetable(student.get("subjects") or [])))
    except Exception as e:
        logger.error(f"timetable_cmd: {e}")

async def mysubjects_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        student = get_student_by_telegram(update.effective_user.id)
        if not student:
            await update.message.reply_text("Use /start to register first.")
            return
        codes = student.get("subjects") or []
        subs = get_subjects_by_codes(codes)
        by_sem = {}
        for s in subs:
            by_sem.setdefault(s["semester"], []).append(s["title"])
        lines = ["--- Your Subjects ---\n"]
        for sem in sorted(by_sem):
            lines.append(f"Semester {sem}:")
            for t in by_sem[sem]:
                lines.append(f"  - {t}")
            lines.append("")
        await update.message.reply_text("\n".join(lines) if subs else "No subjects selected.")
    except Exception as e:
        logger.error(f"mysubjects_cmd: {e}")

async def profile_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        student = get_student_by_telegram(update.effective_user.id)
        if not student:
            await update.message.reply_text("Use /start to register first.")
            return
        await update.message.reply_text(
            f"--- Your Profile ---\n\n"
            f"Name:     {student.get('display_name', '?')}\n"
            f"ID:       {student.get('user_id', '?')}\n"
            f"Semester: {student.get('semester', '?')}\n"
            f"Subjects: {len(student.get('subjects') or [])} enrolled"
        )
    except Exception as e:
        logger.error(f"profile_cmd: {e}")

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "English Department Bot\n\n"
        "/start      - Register or log in\n"
        "/menu       - Main menu\n"
        "/timetable  - View timetable\n"
        "/mysubjects - List subjects\n"
        "/profile    - Your profile\n"
        "/help       - This message\n"
        "/cancel     - Cancel current action"
    )

async def error_handler(update, ctx: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {ctx.error}")
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text("Something went wrong. Try /menu or /start.")
    except Exception:
        pass

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_error_handler(error_handler)

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ENTER_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_id),
                CallbackQueryHandler(try_again, pattern="^try_again$"),
            ],
            ENTER_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_name),
            ],
            SELECT_SEMESTER: [
                CallbackQueryHandler(select_semester, pattern="^sem_\\d+$"),
            ],
            SELECT_SUBJECTS: [
                CallbackQueryHandler(reg_toggle, pattern="^reg_(?!done)"),
                CallbackQueryHandler(reg_done,   pattern="^reg_done$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("menu",       menu_cmd))
    app.add_handler(CommandHandler("timetable",  timetable_cmd))
    app.add_handler(CommandHandler("mysubjects", mysubjects_cmd))
    app.add_handler(CommandHandler("profile",    profile_cmd))
    app.add_handler(CommandHandler("help",       help_cmd))
    app.add_handler(CommandHandler("cancel",     cancel))

    # these run OUTSIDE the conversation (after registration is done)
    app.add_handler(CallbackQueryHandler(menu_handler,    pattern="^menu_"))
    app.add_handler(CallbackQueryHandler(addsem_handler,  pattern="^addsem_\\d+$"))
    app.add_handler(CallbackQueryHandler(addsubj_toggle,  pattern="^addsubj_"))
    app.add_handler(CallbackQueryHandler(adddone_handler, pattern="^adddone$"))
    app.add_handler(CallbackQueryHandler(drop_handler,    pattern="^drop_"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Bot is running...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
