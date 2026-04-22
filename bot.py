import logging
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, filters, ContextTypes
)

# ── Config ────────────────────────────────────────────────────────────────────
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

# ── States ────────────────────────────────────────────────────────────────────
ENTER_ID, ENTER_NAME, ENTER_PHONE, SELECT_SEMESTER, SELECT_SUBJECTS = range(5)
WAITING_FEEDBACK = 10

TIME_LABELS = {"8-10": "8:00-10:00", "10-12": "10:00-12:00", "12-2": "12:00-14:00"}

# ── Supabase helpers ──────────────────────────────────────────────────────────

def db_get(table, params):
    try:
        r = requests.get(f"{SUPA_URL}/{table}", headers=HEADERS, params=params, timeout=10)
        return r.json() if r.ok else []
    except Exception as e:
        logger.error(f"db_get error: {e}")
        return []

def db_post(table, data):
    try:
        r = requests.post(f"{SUPA_URL}/{table}", headers=HEADERS, json=data, timeout=10)
        return r.json() if r.ok else None
    except Exception as e:
        logger.error(f"db_post error: {e}")
        return None

def db_patch(table, match_params, data):
    try:
        r = requests.patch(f"{SUPA_URL}/{table}", headers=HEADERS, params=match_params, json=data, timeout=10)
        return r.ok
    except Exception as e:
        logger.error(f"db_patch error: {e}")
        return False

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
    return db_get("timetable_slots", {
        "subject_code": f"in.({','.join(subject_codes)})",
        "select": "*, subjects(title)"
    })

def save_unregistered(telegram_id, user_id_attempted, username):
    db_post("logs", {
        "type": "unregistered_attempt",
        "user_id": str(telegram_id),
        "action": f"Telegram user @{username or telegram_id} tried Student ID: {user_id_attempted}"
    })

def format_timetable(slots):
    if not slots:
        return "No timetable slots assigned yet. Check back later."
    by_day = {}
    for slot in slots:
        by_day.setdefault(slot["day"], []).append(slot)
    lines = ["Your Timetable\n"]
    for day in ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday"]:
        if day not in by_day:
            continue
        lines.append(f"{day}:")
        for s in sorted(by_day[day], key=lambda x: x["time_slot"]):
            subj = s.get("subjects") or {}
            title = subj.get("title", s["subject_code"]) if isinstance(subj, dict) else s["subject_code"]
            time  = TIME_LABELS.get(s["time_slot"], s["time_slot"])
            room  = f" | Room {s['room']}" if s.get("room") else ""
            lines.append(f"  {time} - {title}{room}")
        lines.append("")
    return "\n".join(lines)

# ── Main menu ─────────────────────────────────────────────────────────────────

def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("My Timetable",   callback_data="menu_timetable")],
        [InlineKeyboardButton("My Subjects",     callback_data="menu_subjects")],
        [InlineKeyboardButton("Add Subjects",    callback_data="menu_add")],
        [InlineKeyboardButton("Drop a Subject",  callback_data="menu_drop")],
        [InlineKeyboardButton("My Profile",      callback_data="menu_profile")],
        [InlineKeyboardButton("Send Feedback",   callback_data="menu_feedback")],
    ])

async def show_main_menu(update, ctx, student):
    name = student.get("display_name") or student.get("user_id", "Student")
    msg = update.callback_query.message if update.callback_query else update.message
    await msg.reply_text(
        f"English Department Portal\nHello, {name}! What would you like to do?",
        reply_markup=main_menu_keyboard()
    )

# ── /start flow ───────────────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        student = get_student_by_telegram(update.effective_user.id)
        if student and student.get("setup_done"):
            await show_main_menu(update, ctx, student)
            return ConversationHandler.END
        await update.message.reply_text(
            "Welcome to the English Department Portal!\n\n"
            "Please enter your Student ID to log in or register:"
        )
        return ENTER_ID
    except Exception as e:
        logger.error(f"start error: {e}")
        await update.message.reply_text("Something went wrong. Please try /start again.")
        return ConversationHandler.END

async def enter_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.message.text.strip()
        student = get_student_by_user_id(user_id)

        if student:
            db_patch("students", {"user_id": f"eq.{user_id}"}, {"telegram_id": update.effective_user.id})
            student["telegram_id"] = update.effective_user.id
            if student.get("setup_done"):
                await show_main_menu(update, ctx, student)
                return ConversationHandler.END
            ctx.user_data["student"] = student
            await update.message.reply_text("Account found! Enter your display name:")
            return ENTER_NAME
        else:
            # Not in database — save the attempt and ask if they want to register
            save_unregistered(
                update.effective_user.id, user_id,
                update.effective_user.username
            )
            ctx.user_data["new_user_id"] = user_id
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("Yes, register me", callback_data="confirm_register")],
                [InlineKeyboardButton("Try a different ID", callback_data="try_again")],
            ])
            await update.message.reply_text(
                f"Student ID '{user_id}' is not in the system.\n\n"
                "This could mean:\n"
                "- Your ID was entered incorrectly\n"
                "- You haven't been added by the admin yet\n\n"
                "Would you like to register anyway? "
                "Your request will be saved and reviewed by the admin.",
                reply_markup=keyboard
            )
            return ENTER_NAME
    except Exception as e:
        logger.error(f"enter_id error: {e}")
        await update.message.reply_text("Error processing ID. Please try again.")
        return ENTER_ID

async def confirm_register(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Enter your full name:")
    return ENTER_NAME

async def try_again(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data.clear()
    await query.edit_message_text("Enter your Student ID again:")
    return ENTER_ID

async def enter_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        name = update.message.text.strip()
        if not name:
            await update.message.reply_text("Please enter a valid name.")
            return ENTER_NAME
        ctx.user_data["display_name"] = name

        if "new_user_id" in ctx.user_data:
            await update.message.reply_text("Enter your phone number (optional, press /skip to skip):")
            return ENTER_PHONE
        else:
            student = ctx.user_data["student"]
            db_patch("students", {"user_id": f"eq.{student['user_id']}"}, {"display_name": name})
            ctx.user_data["student"]["display_name"] = name
            await _ask_semester(update)
            return SELECT_SEMESTER
    except Exception as e:
        logger.error(f"enter_name error: {e}")
        await update.message.reply_text("Error. Please try /start again.")
        return ConversationHandler.END

async def enter_phone(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        phone = update.message.text.strip()
        ctx.user_data["phone"] = phone
        user_id = ctx.user_data["new_user_id"]
        name    = ctx.user_data["display_name"]
        res = db_post("students", {
            "user_id": user_id, "display_name": name, "password": user_id,
            "telegram_id": update.effective_user.id, "subjects": [], "setup_done": False
        })
        ctx.user_data["student"] = (res[0] if isinstance(res, list) else res) or {"user_id": user_id, "display_name": name}
        await _ask_semester(update)
        return SELECT_SEMESTER
    except Exception as e:
        logger.error(f"enter_phone error: {e}")
        await update.message.reply_text("Error. Please try /start again.")
        return ConversationHandler.END

async def skip_phone(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = ctx.user_data["new_user_id"]
        name    = ctx.user_data["display_name"]
        res = db_post("students", {
            "user_id": user_id, "display_name": name, "password": user_id,
            "telegram_id": update.effective_user.id, "subjects": [], "setup_done": False
        })
        ctx.user_data["student"] = (res[0] if isinstance(res, list) else res) or {"user_id": user_id, "display_name": name}
        await _ask_semester(update)
        return SELECT_SEMESTER
    except Exception as e:
        logger.error(f"skip_phone error: {e}")
        await update.message.reply_text("Error. Please try /start again.")
        return ConversationHandler.END

async def _ask_semester(update):
    keyboard = [[InlineKeyboardButton(f"Semester {i}", callback_data=f"sem_{i}")] for i in range(1, 9)]
    await update.message.reply_text("Which semester are you in?", reply_markup=InlineKeyboardMarkup(keyboard))

async def select_semester(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        semester = int(query.data.split("_")[1])
        ctx.user_data["semester"] = semester
        ctx.user_data["selected_subjects"] = []
        subjects = get_subjects_by_semester(semester)
        ctx.user_data["subjects_list"] = subjects
        await show_subject_picker(query, ctx, subjects, semester)
        return SELECT_SUBJECTS
    except Exception as e:
        logger.error(f"select_semester error: {e}")
        return ConversationHandler.END

async def show_subject_picker(query, ctx, subjects, semester):
    selected = ctx.user_data.get("selected_subjects", [])
    keyboard = []
    for s in subjects:
        check = "[X] " if s["code"] in selected else "[ ] "
        keyboard.append([InlineKeyboardButton(f"{check}{s['title']}", callback_data=f"subj_{s['code']}")])
    keyboard.append([InlineKeyboardButton("Done - Save Subjects", callback_data="subjects_done")])
    text = f"Semester {semester} Subjects\nTap to select your enrolled subjects:"
    try:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception:
        await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def toggle_subject(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
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
    except Exception as e:
        logger.error(f"toggle_subject error: {e}")
        return SELECT_SUBJECTS

async def subjects_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
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
        name = student.get("display_name", student["user_id"])
        await query.edit_message_text(
            f"Registration complete!\n\nWelcome, {name}!\n"
            f"You selected {len(selected)} subject(s).\n\nUse /menu anytime to access the portal."
        )
        await show_main_menu(update, ctx, student)
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"subjects_done error: {e}")
        return ConversationHandler.END

# ── Menu callbacks ────────────────────────────────────────────────────────────

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
                await query.message.reply_text("No subjects selected. Use /menu to add some.")
                return
            all_subs = get_subjects_by_codes(codes)
            by_sem = {}
            for s in all_subs:
                by_sem.setdefault(s["semester"], []).append(s["title"])
            lines = ["Your Subjects\n"]
            for sem in sorted(by_sem):
                lines.append(f"Semester {sem}:")
                for t in by_sem[sem]:
                    lines.append(f"  - {t}")
                lines.append("")
            await query.message.reply_text("\n".join(lines))

        elif action == "menu_profile":
            sem = student.get("semester", "?")
            uid = student.get("user_id", "?")
            name = student.get("display_name", "?")
            subj_count = len(student.get("subjects") or [])
            await query.message.reply_text(
                f"Your Profile\n\n"
                f"Name:     {name}\n"
                f"ID:       {uid}\n"
                f"Semester: {sem}\n"
                f"Subjects: {subj_count} enrolled\n\n"
                f"To update your subjects use Add/Drop from the menu."
            )

        elif action == "menu_add":
            ctx.user_data["student"] = student
            keyboard = [[InlineKeyboardButton(f"Semester {i}", callback_data=f"addsem_{i}")] for i in range(1, 9)]
            await query.message.reply_text("Which semester to add subjects from?",
                reply_markup=InlineKeyboardMarkup(keyboard))

        elif action == "menu_drop":
            codes = student.get("subjects") or []
            if not codes:
                await query.message.reply_text("You have no subjects to drop.")
                return
            all_subs = get_subjects_by_codes(codes)
            keyboard = [[InlineKeyboardButton(f"Remove: {s['title']}", callback_data=f"drop_{s['code']}")] for s in all_subs]
            keyboard.append([InlineKeyboardButton("Cancel", callback_data="menu_cancel")])
            await query.message.reply_text("Select a subject to drop:", reply_markup=InlineKeyboardMarkup(keyboard))

        elif action == "menu_feedback":
            ctx.user_data["waiting_feedback"] = True
            await query.message.reply_text(
                "Send your message, question, or feedback.\n"
                "It will be saved and reviewed by the admin.\n\n"
                "Type /cancel to cancel."
            )

        elif action == "menu_cancel":
            await show_main_menu(update, ctx, student)

    except Exception as e:
        logger.error(f"menu_handler error: {e}")
        await query.message.reply_text("Something went wrong. Please try again.")

async def add_semester_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
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
            check = "[X] " if s["code"] in current else "[ ] "
            keyboard.append([InlineKeyboardButton(f"{check}{s['title']}", callback_data=f"addsubj_{s['code']}")])
        keyboard.append([InlineKeyboardButton("Save", callback_data="adddone")])
        await query.edit_message_text(f"Semester {semester} - tap to add/remove:",
            reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        logger.error(f"add_semester error: {e}")

async def add_subject_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
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
            check = "[X] " if s["code"] in selected else "[ ] "
            keyboard.append([InlineKeyboardButton(f"{check}{s['title']}", callback_data=f"addsubj_{s['code']}")])
        keyboard.append([InlineKeyboardButton("Save", callback_data="adddone")])
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        logger.error(f"add_subject_toggle error: {e}")

async def add_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        selected = ctx.user_data.get("selected_subjects", [])
        student = ctx.user_data.get("student") or get_student_by_telegram(update.effective_user.id)
        db_patch("students", {"user_id": f"eq.{student['user_id']}"}, {"subjects": selected})
        await query.edit_message_text(f"Subjects updated! You now have {len(selected)} subject(s).")
        student["subjects"] = selected
        await show_main_menu(update, ctx, student)
    except Exception as e:
        logger.error(f"add_done error: {e}")

async def drop_subject(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
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
        await query.edit_message_text(f"Dropped '{title}' from your subjects.")
        student["subjects"] = current
        await show_main_menu(update, ctx, student)
    except Exception as e:
        logger.error(f"drop_subject error: {e}")

# ── Feedback handler ──────────────────────────────────────────────────────────

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if ctx.user_data.get("waiting_feedback"):
            ctx.user_data["waiting_feedback"] = False
            student = get_student_by_telegram(update.effective_user.id)
            uid = student["user_id"] if student else str(update.effective_user.id)
            db_post("logs", {
                "type": "feedback",
                "user_id": uid,
                "action": f"Feedback: {update.message.text}"
            })
            await update.message.reply_text(
                "Your message has been saved! The admin will review it.\n\nUse /menu to go back."
            )
            return
        await update.message.reply_text("Use /menu to open the portal, or /start to register.")
    except Exception as e:
        logger.error(f"handle_message error: {e}")

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("Cancelled. Use /menu to go back.")
    return ConversationHandler.END

# ── Shortcut commands ─────────────────────────────────────────────────────────

async def timetable_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        student = get_student_by_telegram(update.effective_user.id)
        if not student:
            await update.message.reply_text("Use /start to register first.")
            return
        slots = get_timetable(student.get("subjects") or [])
        await update.message.reply_text(format_timetable(slots))
    except Exception as e:
        logger.error(f"timetable_cmd error: {e}")
        await update.message.reply_text("Could not load timetable. Try again later.")

async def mysubjects_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        student = get_student_by_telegram(update.effective_user.id)
        if not student:
            await update.message.reply_text("Use /start to register first.")
            return
        codes = student.get("subjects") or []
        if not codes:
            await update.message.reply_text("No subjects selected.")
            return
        all_subs = get_subjects_by_codes(codes)
        by_sem = {}
        for s in all_subs:
            by_sem.setdefault(s["semester"], []).append(s["title"])
        lines = ["Your Subjects\n"]
        for sem in sorted(by_sem):
            lines.append(f"Semester {sem}:")
            for t in by_sem[sem]:
                lines.append(f"  - {t}")
            lines.append("")
        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        logger.error(f"mysubjects_cmd error: {e}")

async def menu_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        student = get_student_by_telegram(update.effective_user.id)
        if not student or not student.get("setup_done"):
            await update.message.reply_text("Use /start to register first.")
            return
        await show_main_menu(update, ctx, student)
    except Exception as e:
        logger.error(f"menu_cmd error: {e}")
        await update.message.reply_text("Something went wrong. Try /start.")

async def profile_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        student = get_student_by_telegram(update.effective_user.id)
        if not student:
            await update.message.reply_text("Use /start to register first.")
            return
        sem = student.get("semester", "?")
        uid = student.get("user_id", "?")
        name = student.get("display_name", "?")
        subj_count = len(student.get("subjects") or [])
        await update.message.reply_text(
            f"Your Profile\n\n"
            f"Name:     {name}\n"
            f"ID:       {uid}\n"
            f"Semester: {sem}\n"
            f"Subjects: {subj_count} enrolled"
        )
    except Exception as e:
        logger.error(f"profile_cmd error: {e}")

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "English Department Bot - Commands\n\n"
        "/start      - Register or log in\n"
        "/menu       - Open main menu\n"
        "/timetable  - View your timetable\n"
        "/mysubjects - List your subjects\n"
        "/profile    - View your profile\n"
        "/help       - Show this message\n"
        "/cancel     - Cancel current action"
    )

# ── Error handler ─────────────────────────────────────────────────────────────

async def error_handler(update, ctx: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Global error: {ctx.error}")
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "Something went wrong. Please try /menu or /start."
            )
    except Exception:
        pass

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_error_handler(error_handler)

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ENTER_ID:        [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_id),
                CallbackQueryHandler(confirm_register, pattern="^confirm_register$"),
                CallbackQueryHandler(try_again,        pattern="^try_again$"),
            ],
            ENTER_NAME:      [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_name),
                CallbackQueryHandler(confirm_register, pattern="^confirm_register$"),
            ],
            ENTER_PHONE:     [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_phone),
                CommandHandler("skip", skip_phone),
            ],
            SELECT_SEMESTER: [CallbackQueryHandler(select_semester, pattern="^sem_")],
            SELECT_SUBJECTS: [
                CallbackQueryHandler(toggle_subject, pattern="^subj_"),
                CallbackQueryHandler(subjects_done,  pattern="^subjects_done$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("timetable",  timetable_cmd))
    app.add_handler(CommandHandler("mysubjects", mysubjects_cmd))
    app.add_handler(CommandHandler("menu",       menu_cmd))
    app.add_handler(CommandHandler("profile",    profile_cmd))
    app.add_handler(CommandHandler("help",       help_cmd))
    app.add_handler(CommandHandler("cancel",     cancel))
    app.add_handler(CallbackQueryHandler(menu_handler,        pattern="^menu_"))
    app.add_handler(CallbackQueryHandler(add_semester_handler, pattern="^addsem_"))
    app.add_handler(CallbackQueryHandler(add_subject_toggle,   pattern="^addsubj_"))
    app.add_handler(CallbackQueryHandler(add_done,             pattern="^adddone$"))
    app.add_handler(CallbackQueryHandler(drop_subject,         pattern="^drop_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Bot is running...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
