from django.contrib import admin

from .models import *

# Register your models here.
admin.site.register(OrchestrationRun)
admin.site.register(ExecutorAgentRun)
admin.site.register(AgenticTaskSet)
admin.site.register(ExecutorScreenshot)