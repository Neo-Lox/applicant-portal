-- Add email throttle timestamp to applications
ALTER TABLE applications 
  ADD COLUMN IF NOT EXISTS last_candidate_upload_email_at TIMESTAMPTZ;
