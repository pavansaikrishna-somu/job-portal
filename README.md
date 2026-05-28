
 # CareerConnect 🚀

A modern full-stack job portal platform built using Django and MongoDB Atlas.

CareerConnect connects recruiters and job seekers through job posting, applications, resume uploads, applicant tracking, and role-based dashboards.

---

## ✨ Features

- Role-based Authentication
- Recruiter & Jobseeker Dashboards
- Job Posting & Management
- Resume Upload System
- Applicant Tracking System (ATS)
- Professional Profile Management
- JWT Authentication APIs
- Responsive Premium UI

---

## 🛠️ Tech Stack

- Django
- Django REST Framework
- MongoDB Atlas
- MongoEngine
- Bootstrap 5
- Chart.js
- HTML/CSS/JavaScript

---

## ⚡ Installation

```bash
git clone <repository-url>
cd job_portal/backend
```

Create virtual environment:

```bash
python -m venv .venv
```

Activate environment:

### Windows

```bash
.\.venv\Scripts\activate
```

### Linux/macOS

```bash
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Run migrations:

```bash
python manage.py migrate
```

Seed sample data:

```bash
python manage.py seed_data
```

Run server:

```bash
python manage.py runserver
```

Open:

```text
http://127.0.0.1:8000/
```

---

## 👤 Demo Accounts

### Recruiter

```text
recruiter1@example.com
Password@123
```

### Jobseeker

```text
jobseeker1@example.com
Password@123
```

---

## 📂 Main Modules

- Users
- Jobs
- Applications
- Dashboards
- Profiles
- APIs

---

## 📄 Media Files

Uploaded files are stored in:

```text
backend/media/
```

Includes:
- resumes
- profile images
- company logos

---

## 🚀 Future Enhancements

- AI Resume Screening
- Email Notifications
- Interview Scheduling
- Real-time Chat
- Advanced Filters

---

## 👨‍💻 Developed With Passion

CareerConnect is a modern ATS-style recruitment platform inspired by real-world hiring systems.
