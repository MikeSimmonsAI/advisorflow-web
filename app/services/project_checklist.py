"""Owner checklist metadata on existing Launch Board projects."""
from app.services import launch_board as lb

PLATFORM_LOGIN = "platform@evosyspro.live"


def can_manage(user):
    """The owner and the named EvoSys operating account share the checklist."""
    return user.is_active and (
        user.role == "god_admin" or
        (user.role == "org_admin" and (user.email or "").strip().lower() == PLATFORM_LOGIN)
    )


def status_for(project):
    if project.get("checklist_status"):
        return project["checklist_status"]
    if lb.product_state(project) == "complete":
        return "done"
    if project["lane"] == "active" or lb.product_state(project) == "in_progress":
        return "in_progress"
    return "todo"


def save(board, project_id, data, actor):
    name = data["name"].strip()
    tasks = [{"title": task["title"].strip(), "done": task["done"]} for task in data["tasks"]]
    if not name or any(not task["title"] for task in tasks):
        raise lb.BoardError("Project and task names are required.")
    if data["status"] == "done" and any(not task["done"] for task in tasks):
        raise lb.BoardError("Check off every task before marking the project done.")
    if project_id is None:
        project = lb.add_project(board, name, data["summary"], actor)
    else:
        project = lb._find(board, project_id)
        if project["lane"] == "archived":
            raise lb.BoardError("This project is archived.")
        if project["name"] != name:
            raise lb.BoardError("The project name is used by the Launch Board.")
    project.update(name=name, summary=data["summary"].strip(),
                   checklist_status=data["status"], checklist_tasks=tasks)
    lb._event(project, actor, "checklist_saved",
              f'{data["status"]} · {sum(task["done"] for task in tasks)}/{len(tasks)} tasks checked', None)
    return project["id"]


# These entries describe specific milestones, with upcoming production checks
# left open. Matching existing projects keep their current data.
STARTER = (
    ("EvoSys Pro mobile install", "done", (
        ("Publish the /app install entry and EvoSys Pro icon (#648)", True),
        ("Verify the production install manifest and launch path", True),
    )),
    ("Restland", "in_progress", (
        ("Merge the 8:30 AM–7 PM Central daily sending window (#666)", True),
        ("Verify morning processing and delivery in production", False),
    )),
    ("Sales Workspace", "in_progress", (
        ("Select the six-screen My Day design", True),
        ("Match the approved design on desktop and mobile", False),
        ("Verify where staged leads appear in the workspace", False),
    )),
    ("Deal Builders and Sales Onboarding", "in_progress", (
        ("Separate the executive and salesperson Deal Builders", True),
        ("Verify Program Access and commission visibility by role", False),
        ("Finish executive sales onboarding", False),
    )),
    ("VOLT Marketing", "in_progress", (
        ("Launch the applicant and Operations portals (#537, #540)", True),
        ("Verify live Recruiting and Consumer Leads sheet sync", False),
        ("Verify recruiting lead access for authorized staff", False),
    )),
    ("Divine Shine Elite Cleaning", "todo", (
        ("Confirm campaign start and pilot schedule", False),
        ("Track 3–5 qualified appointments during the pilot", False),
    )),
    ("SCI SMS Recovery", "done", (
        ("Launch the SMS Recovery screen (#485)", True),
    )),
    ("Blue Ridge Gold & Jewelry", "done", (
        ("Ship the live workspace conversion (#543, #553)", True),
    )),
    ("Cleaning Acquisition import and assignment", "done", (
        ("Ship the Master Pool importer (#473)", True),
        ("Ship client assignment and reclaim (#472)", True),
    )),
    ("AI phone receptionist coaching", "wishlist", (
        ("Explore coaching the AI after calls so future responses improve", False),
    )),
)


def import_starter(board, actor):
    names = {project["name"].lower() for project in board["projects"]}
    added = 0
    for name, status, tasks in STARTER:
        if name.lower() in names:
            continue
        save(board, None, {
            "name": name, "summary": "", "status": status,
            "tasks": [{"title": title, "done": done} for title, done in tasks],
        }, actor)
        added += 1
    return added
