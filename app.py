from flask import Flask, render_template, request, redirect, flash, url_for, session, make_response
from functools import wraps
import json
import os
import csv
import io

app = Flask(__name__)
app.secret_key = "super_secret_key_tournament"
DATA_FILE = "data.json"

# ─── Event definitions ────────────────────────────────────────────────────────
EVENTS = {
    "100m_sprint":    "100m Sprint",
    "800m_run":       "800m Run",
    "shot_put":       "Shot Put",
    "long_jump":      "Long Jump",
    "spelling_bee":   "Spelling Bee",
    "math_bee":       "Math Bee",
    "debate":         "Debate",
    "science_trivia": "Science Trivia",
}

# ─── Hardcoded credentials ────────────────────────────────────────────────────
AUTH_USERNAME = "12345678"
AUTH_PASSWORD = "12345678"

# ─── Authentication helpers ───────────────────────────────────────────────────
def login_required(f):
    """Decorator that redirects unauthenticated users to the login page."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            flash("Please log in to access this page.", "error")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

# ─── Data helpers ─────────────────────────────────────────────────────────────
def _migrate_participant(p):
    """
    Ensure a participant has the new event_scores dict.
    Handles legacy records that only have a flat 'score' integer.
    """
    if "event_scores" not in p:
        legacy = p.pop("score", 0)
        # Spread legacy score across event_1 as a best-effort migration
        p["event_scores"] = {k: 0 for k in EVENTS}
        if legacy:
            p["event_scores"]["event_1"] = legacy
    if "assigned_event" not in p:
        p["assigned_event"] = None
    # Remove stale flat score key if still present
    p.pop("score", None)
    return p

def load_data():
    """Load participant data from the JSON file, migrating old records if needed."""
    if not os.path.exists(DATA_FILE):
        return {"participants": []}
    with open(DATA_FILE, "r") as f:
        data = json.load(f)
    data["participants"] = [_migrate_participant(p) for p in data.get("participants", [])]
    return data

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

def total_score(p):
    """Sum all per-event scores for a participant."""
    return sum(p.get("event_scores", {}).values())

# ─── Authentication routes ────────────────────────────────────────────────────
@app.route('/login', methods=['GET', 'POST'])
def login():
    """Display and process the login form."""
    if session.get("logged_in"):
        return redirect(url_for("dashboard"))
    if request.method == 'POST':
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        if username == AUTH_USERNAME and password == AUTH_PASSWORD:
            session["logged_in"] = True
            flash("Welcome back! You are now logged in.", "success")
            return redirect(url_for("dashboard"))
        else:
            flash("Invalid credentials. Please try again.", "error")
    return render_template("login.html")

@app.route('/logout')
def logout():
    """Clear the session and redirect to the login page."""
    session.clear()
    flash("You have been logged out successfully.", "success")
    return redirect(url_for("login"))

# ─── Application routes ───────────────────────────────────────────────────────
@app.route('/')
@login_required
def dashboard():
    """Render the main dashboard with registration statistics."""
    data = load_data()
    teams = [p for p in data.get("participants", []) if p.get("type") == "team"]
    individuals = [p for p in data.get("participants", []) if p.get("type") == "individual"]
    return render_template("dashboard.html", teams_count=len(teams), individuals_count=len(individuals))

@app.route('/register', methods=['GET', 'POST'])
@login_required
def register():
    """Handle participant registration."""
    data = load_data()
    if request.method == 'POST':
        name       = request.form.get("name", "").strip()
        p_type     = request.form.get("type")
        entry      = request.form.get("entry")
        # assigned_event is only meaningful for single-entry participants
        assigned_event = request.form.get("assigned_event") if entry == "single" else None

        # Validate assigned_event for single-entry participants
        if entry == "single" and assigned_event not in EVENTS:
            flash("Please select a valid assigned event for Single Event entry.", "error")
            return redirect(url_for('register'))

        # Check current counts
        teams       = [p for p in data.get("participants", []) if p.get("type") == "team"]
        individuals = [p for p in data.get("participants", []) if p.get("type") == "individual"]

        # Reject duplicate participant/team names (case-insensitive)
        existing_names = [p["name"].strip().lower() for p in data.get("participants", [])]
        if name.lower() in existing_names:
            flash("A participant or team with that name already exists.", "error")
            return redirect(url_for('register'))

        if p_type == "team" and len(teams) >= 4:
            flash("Team registration is full (Max 4).", "error")
            return redirect(url_for('register'))

        if p_type == "individual" and len(individuals) >= 20:
            flash("Individual registration is full (Max 20).", "error")
            return redirect(url_for('register'))

        new_id = len(data.get("participants", [])) + 1
        data["participants"].append({
            "id": new_id,
            "name": name,
            "type": p_type,
            "entry": entry,
            "assigned_event": assigned_event,
            "event_scores": {k: 0 for k in EVENTS},
        })

        save_data(data)
        flash("Registration successful!", "success")
        return redirect(url_for('dashboard'))

    return render_template("register.html", events=EVENTS)

@app.route('/record-scores', methods=['GET', 'POST'])
@login_required
def record_scores():
    """Route for updating scores based on placements, per event."""
    data = load_data()
    if request.method == 'POST':
        # Parse and validate numeric inputs
        try:
            participant_id = int(request.form.get("participant_id"))
            rank = int(request.form.get("rank"))
        except (ValueError, TypeError):
            flash("Invalid input: participant and rank must be numeric values.", "error")
            return redirect(url_for('record_scores'))

        # Validate rank range
        if rank < 1 or rank > 20:
            flash("Invalid rank: must be between 1 and 20.", "error")
            return redirect(url_for('record_scores'))

        # Validate event_id
        event_id = request.form.get("event_id", "").strip()
        if event_id not in EVENTS:
            flash("Invalid event selected.", "error")
            return redirect(url_for('record_scores'))

        # Find the participant
        participant = next((p for p in data.get("participants", []) if p["id"] == participant_id), None)
        if not participant:
            flash("Participant not found.", "error")
            return redirect(url_for('record_scores'))

        # ── Entry-type enforcement ────────────────────────────────────────────
        if participant["entry"] == "single":
            assigned = participant.get("assigned_event")
            if event_id != assigned:
                event_label = EVENTS.get(assigned, assigned)
                flash(
                    f"'{participant['name']}' is registered for Single Event only "
                    f"({event_label}). Score cannot be recorded for another event.",
                    "error"
                )
                return redirect(url_for('record_scores'))

        # ── Points map ───────────────────────────────────────────────────────
        points_map = {1: 10, 2: 8, 3: 6, 4: 4}
        points = points_map.get(rank, 2)

        # Save score to the specific event slot (overwrite, not accumulate)
        participant["event_scores"][event_id] = participant["event_scores"].get(event_id, 0) + points

        save_data(data)
        flash(
            f"Score recorded! {points} pts awarded to {participant['name']} "
            f"for {EVENTS[event_id]} (Rank #{rank}).",
            "success"
        )
        return redirect(url_for('leaderboard'))

    participants = data.get("participants", [])
    # Attach computed total for display in the dropdown
    for p in participants:
        p["total"] = total_score(p)

    return render_template("record_scores.html", participants=participants, events=EVENTS)

@app.route('/leaderboard')
@login_required
def leaderboard():
    """Display the sorted leaderboard with proper tie-break ranks."""
    data = load_data()
    participants = data.get("participants", [])

    # Compute totals for sorting
    for p in participants:
        p["score"] = total_score(p)

    sorted_participants = sorted(participants, key=lambda k: k["score"], reverse=True)

    ranked_participants = []
    current_rank = 1
    for i, p in enumerate(sorted_participants):
        if i > 0 and p["score"] == sorted_participants[i - 1]["score"]:
            rank = ranked_participants[i - 1]["display_rank"]
        else:
            rank = current_rank
        ranked_participants.append({**p, "display_rank": rank})
        current_rank += 1

    return render_template("leaderboard.html", participants=ranked_participants, events=EVENTS)

@app.route('/reset-data', methods=['POST'])
@login_required
def reset_data():
    """Wipe all tournament participants and scores, then redirect back."""
    save_data({"participants": []})
    flash("All tournament data has been reset successfully.", "success")
    redirect_to = request.form.get("next", "dashboard")
    return redirect(url_for(redirect_to))

@app.route('/export-csv')
@login_required
def export_csv():
    """Generate and download a CSV of the ranked leaderboard."""
    data = load_data()
    participants = data.get("participants", [])
    for p in participants:
        p["score"] = total_score(p)

    sorted_participants = sorted(participants, key=lambda k: k["score"], reverse=True)

    ranked = []
    current_rank = 1
    for i, p in enumerate(sorted_participants):
        if i > 0 and p["score"] == sorted_participants[i - 1]["score"]:
            rank = ranked[i - 1]["display_rank"]
        else:
            rank = current_rank
        ranked.append({**p, "display_rank": rank})
        current_rank += 1

    output = io.StringIO()
    writer = csv.writer(output)
    # Header includes per-event columns
    writer.writerow(["Rank", "Participant", "Type", "Entry Option", "Assigned Event",
                     "Event 1", "Event 2", "Event 3", "Event 4", "Event 5", "Total Points"])
    for p in ranked:
        es = p.get("event_scores", {})
        assigned = EVENTS.get(p.get("assigned_event", ""), "All Events")
        writer.writerow([
            p["display_rank"], p["name"], p["type"].title(), p["entry"].title(),
            assigned,
            es.get("event_1", 0), es.get("event_2", 0), es.get("event_3", 0),
            es.get("event_4", 0), es.get("event_5", 0),
            p["score"]
        ])

    response = make_response(output.getvalue())
    response.headers["Content-Disposition"] = "attachment; filename=tournament_leaderboard.csv"
    response.headers["Content-Type"] = "text/csv"
    return response

if __name__ == '__main__':
    app.run(debug=True, port=5000)
