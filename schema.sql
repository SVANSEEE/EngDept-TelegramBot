-- ============================================================
-- English Department Portal - Supabase Schema
-- ============================================================

-- Students table (same structure as the Android app)
CREATE TABLE IF NOT EXISTS students (
  id            uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id       text UNIQUE NOT NULL,         -- e.g. "2021-ENG-045"
  display_name  text,
  password      text,                         -- defaults to user_id
  semester      int,
  subjects      text[] DEFAULT '{}',          -- array of subject codes
  grades        jsonb DEFAULT '{}',           -- {subjectCode: {grade, gradedBy, gradedAt}}
  setup_done    boolean DEFAULT false,
  telegram_id   bigint UNIQUE,               -- Telegram chat ID
  registered_at timestamptz DEFAULT now()
);

-- Subjects table (all 47 subjects from the app)
CREATE TABLE IF NOT EXISTS subjects (
  code      text PRIMARY KEY,
  title     text NOT NULL,
  semester  int NOT NULL
);

-- Timetable slots
CREATE TABLE IF NOT EXISTS timetable_slots (
  id          uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  subject_code text REFERENCES subjects(code),
  day         text NOT NULL,    -- Sunday, Monday, etc.
  time_slot   text NOT NULL,    -- "8-10", "10-12", "12-2"
  room        text,
  teacher_id  text,
  created_at  timestamptz DEFAULT now()
);

-- ============================================================
-- Seed: all 47 subjects
-- ============================================================
INSERT INTO subjects (code, title, semester) VALUES
  ('120101', 'Basic Writing Skills, I',                    1),
  ('120102', 'Listening and Speaking, I',                  1),
  ('120103', 'Reading Comprehension, I',                   1),
  ('120104', 'Fundamentals of English Grammar, I',         1),
  ('101101', 'Arabic Language',                            1),
  ('110101', 'General Psychology',                         1),
  ('220201', 'Paragraph Writing',                          2),
  ('220203', 'Listening and Speaking, II',                 2),
  ('220204', 'Reading Comprehension, II',                  2),
  ('220205', 'Fundamentals of English Grammar, II',        2),
  ('220202', 'Arab-Islamic Civilization',                  2),
  ('220207', 'Arabic-Based Writing Skills',                2),
  ('320301', 'Essay Writing',                              3),
  ('320302', 'Introduction to Linguistics',                3),
  ('320303', 'Listening and Speaking, III',                3),
  ('320304', 'Reading Comprehension, III',                 3),
  ('320305', 'Fundamentals of English Grammar, III',       3),
  ('320306', 'Introduction to Classical English Literature', 3),
  ('420401', 'Creative Writing',                           4),
  ('420402', 'Literary Readings',                          4),
  ('420403', 'Pronunciation Skills',                       4),
  ('420404', 'Advanced Grammar',                           4),
  ('420405', 'Listening and Speaking, IV',                 4),
  ('420406', 'Introduction to Translation',                4),
  ('520501', 'Translation Studies',                        5),
  ('520502', 'Introduction to ESP',                        5),
  ('520503', 'Phonetics and Phonology',                    5),
  ('520504', 'Modern English Literature',                  5),
  ('520505', 'Introduction to Morphology',                 5),
  ('520506', 'Introduction to Applied Linguistics',        5),
  ('620601', 'Language Acquisition',                       6),
  ('620602', 'Introduction to Syntax',                     6),
  ('620603', 'Teaching Methodologies',                     6),
  ('620604', 'Semantics and Pragmatics',                   6),
  ('620605', 'Classical International Literature',         6),
  ('720701', 'Sociolinguistics',                           7),
  ('720702', 'Computer Skills',                            7),
  ('720703', 'Readings in English Language Studies',       7),
  ('720704', 'Research Methodology',                       7),
  ('720705', 'Language Testing and Assessment',            7),
  ('820801', 'Graduate Research Project',                  8),
  ('820802', 'Practicum',                                  8),
  ('820803', 'Teaching English to Young Learners',         8),
  ('820804', 'English Language in Modern Media',           8),
  ('820805', 'Teaching English as a Foreign Language (EFL)', 8)
ON CONFLICT (code) DO NOTHING;

-- ============================================================
-- Row Level Security (optional but recommended)
-- ============================================================
ALTER TABLE students ENABLE ROW LEVEL SECURITY;
ALTER TABLE subjects ENABLE ROW LEVEL SECURITY;
ALTER TABLE timetable_slots ENABLE ROW LEVEL SECURITY;

-- Allow full access via service role (used by the bot)
CREATE POLICY "service_role_all" ON students FOR ALL USING (true);
CREATE POLICY "service_role_all" ON subjects FOR ALL USING (true);
CREATE POLICY "service_role_all" ON timetable_slots FOR ALL USING (true);
