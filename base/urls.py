from django.urls import path

from django.contrib.auth import views as auth_views
from django.contrib.auth.views import LoginView, LogoutView

from . import views
from .views import CustomLoginView, register


urlpatterns = [
    path('', views.home, name="home"),
    path("login/", CustomLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("register/", register, name="register"),
    path('dashboard/', views.dashboard, name="dashboard"),
    path("dashboard/runs/",views.orchestration_runs_partial, name="orchestration_runs_partial"),
    path("orchestration_run/new/",views.create_orchestration_run, name="create_orchestration_run"),
    path("orchestration_run/success/",views.orchestration_run_success, name="orchestration_run_success"),
    path("runs/", views.run_list, name="run_list"),
    path("runs/partial/", views.run_list_partial, name="run_list_partial"),
    path("runs/<uuid:run_id>/", views.run_detail, name="run_detail"),
    path("runs/<uuid:run_id>/task-sets/<uuid:task_set_id>/", views.task_set_detail, name="task_set_detail"),
    path("runs/<uuid:run_id>/task-sets/<uuid:task_set_id>/executors/<uuid:executor_id>/", views.executor_detail, name="executor_detail"),
]
