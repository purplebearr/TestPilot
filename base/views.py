import os

from django.shortcuts import render, get_object_or_404
from django.contrib.auth.forms import UserCreationForm
from django.shortcuts import render, redirect
from django.contrib.auth.views import LoginView
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.template.loader import render_to_string

from .models import OrchestrationRun, AgenticTaskSet, ExecutorAgentRun
from .forms import OrchestrationRunForm

from .tasks import run_orchestration_run

# Create your views here.

def home(request):
    return render(request, 'base/home.html')

class CustomLoginView(LoginView):
    template_name = "registration/login.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("home")
        
        return super().dispatch(request, *args, **kwargs)

def register(request):
    if request.user.is_authenticated:
        return redirect("home")
    
    if request.method == "POST":
        form = UserCreationForm(request.POST)

        if form.is_valid():
            form.save()
            return redirect("login")
    else:
        form = UserCreationForm()

    return render(request, "registration/register.html", {"form": form})

#------------------------------------------------------------

@login_required
def dashboard(request):
    if request.method == "POST":
        form = OrchestrationRunForm(request.POST)

        if form.is_valid():
            run = form.save(commit=False)
            run.status = OrchestrationRun.Status.PENDING
            run.user = request.user
            run.save()

            run_orchestration_run(run.id)

            return redirect("dashboard")

    else:
        form = OrchestrationRunForm()

    runs = (
        OrchestrationRun.objects
        .filter(user=request.user)
        .order_by("-created_at")
    )

    return render(
        request,
        "base/dashboard.html",
        {
            "runs": runs,
            "form": form,
        },
    )


@login_required
def orchestration_runs_partial(request):
    runs = (
        OrchestrationRun.objects
        .filter(user=request.user)
        .order_by("-created_at")
    )

    print(runs)

    return render(
        request,
        "base/orchestration_runs.html",
        {"runs": runs},
    )


#--------------------------------------------------------------

@login_required
def create_orchestration_run(request):
    if request.method == "POST":
        form = OrchestrationRunForm(request.POST)

        if form.is_valid():
            run = form.save(commit=False)
            run.status = OrchestrationRun.Status.PENDING
            run.user = request.user
            run.save()

            # Trigger the orchestration run asynchronously using Huey
            run_orchestration_run(run.id)

            return redirect("orchestration_run_success")

    else:
        form = OrchestrationRunForm()

    return render(
        request,
        "base/create_run.html",
        {"form": form},
    )

@login_required
def orchestration_run_success(request):
    return render(
        request,
        "base/run_success.html",
    )


#------------------------------------------------------------

def _screenshot_map(executor):
    """filename (e.g. '03_registration_form_visible.png') -> ExecutorScreenshot,
    so execution_log entries (which only know filenames) can be matched back
    to the actual stored images/URLs."""
    return {os.path.basename(shot.image.name): shot for shot in executor.screenshots.all()}
 
 
def _presented_screenshots(executor, limit=None):
    """The curated highlight subset from this executor's execution_log,
    resolved to ExecutorScreenshot objects. Falls back to all screenshots
    if the log has no presented_screenshots (e.g. older runs)."""
    log = executor.execution_log or {}
    filenames = log.get("presented_screenshots") or []
    shot_map = _screenshot_map(executor)
    shots = [shot_map[fn] for fn in filenames if fn in shot_map]
    if not shots:
        shots = list(executor.screenshots.all())
    return shots[:limit] if limit else shots
 
 
def _log_steps(executor):
    """execution_log['steps'] enriched with the resolved ExecutorScreenshot
    objects for each step, so the template can render inline thumbnails
    instead of just filenames."""
    log = executor.execution_log or {}
    shot_map = _screenshot_map(executor)
    steps = []
    for raw in log.get("steps", []):
        steps.append({
            **raw,
            "screenshot_objs": [shot_map[fn] for fn in raw.get("screenshots", []) if fn in shot_map],
        })
    return steps


@login_required
def run_list(request):
    runs = OrchestrationRun.objects.only(
        "id", "user_inquiry", "status", "started_at",
        "total_input_tokens", "total_output_tokens",
    )
    return render(request, "base/run_list.html", {"runs": runs})


@login_required
def run_list_partial(request):
    runs = list(
        OrchestrationRun.objects.only(
            "id", "user_inquiry", "status", "started_at",
            "total_input_tokens", "total_output_tokens",
        )
    )
    has_active = any(
        r.status in (OrchestrationRun.Status.PENDING, OrchestrationRun.Status.RUNNING)
        for r in runs
    )
    html = render_to_string("base/run_rows.html", {"runs": runs}, request=request)
    return JsonResponse({"html": html, "has_active": has_active})
 
 
@login_required
def run_detail(request, run_id):
    run = get_object_or_404(
        OrchestrationRun.objects.prefetch_related(
            "task_sets__executor_runs__screenshots",
        ),
        pk=run_id,
    )
 
    # Pull each executor's own curated "presented_screenshots" (the
    # highlight reel it already picked out of its full run) rather than
    # guessing -- a couple of frames per agent, in round/agent order,
    # capped so the top-level page stays a quick skim.
    highlight_screenshots = []
    for task_set in run.task_sets.all():
        for executor in task_set.executor_runs.all():
            highlight_screenshots.extend(_presented_screenshots(executor, limit=3))
    highlight_screenshots = highlight_screenshots[:8]
 
    return render(request, "base/run_detail.html", {
        "run": run,
        "highlight_screenshots": highlight_screenshots,
    })
 
@login_required
def task_set_detail(request, run_id, task_set_id):
    task_set = get_object_or_404(
        AgenticTaskSet.objects.select_related("orchestration_run").prefetch_related(
            "executor_runs__persona",
            "executor_runs__screenshots",
        ),
        pk=task_set_id,
        orchestration_run_id=run_id,
    )
    round_screenshots = []
    for executor in task_set.executor_runs.all():
        round_screenshots.extend(_presented_screenshots(executor, limit=6))
    round_screenshots = round_screenshots[:16]
 
    return render(request, "base/task_set_detail.html", {
        "task_set": task_set,
        "round_screenshots": round_screenshots,
    })
 
@login_required
def executor_detail(request, run_id, task_set_id, executor_id):
    executor = get_object_or_404(
        ExecutorAgentRun.objects.select_related("task_set__orchestration_run", "persona")
        .prefetch_related("screenshots"),
        pk=executor_id,
        task_set_id=task_set_id,
        task_set__orchestration_run_id=run_id,
    )
    return render(request, "base/executor_detail.html", {
        "executor": executor,
        "log_steps": _log_steps(executor),
    })
 