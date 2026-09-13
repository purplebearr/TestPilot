# TestPilot

**TestPilot** is an agentic web testing system that allows developers to describe what they want to test using natural language and lets a team of AI agents determine how to test it.

Instead of manually writing and maintaining a collection of browser-based test scripts, TestPilot turns a high-level testing request into a testing strategy, delegates individual tasks to browser-controlling AI agents, and produces a report containing the results and supporting evidence.

---

## Table of Contents

- [What TestPilot Does](#what-testpilot-does)
- [Testing at Any Level of Specificity](#testing-at-any-level-of-specificity)
- [Architecture](#architecture)
- [Agents](#agents)
- [End-to-End Workflow](#end-to-end-workflow)
- [Screenshot & Evidence Management](#screenshot--evidence-management)
- [Getting Started](#️-getting-started)
- [Installation](#installation)
- [Running TestPilot](#-running-testpilot)
- [Using TestPilot](#-using-testpilot)

---

## What TestPilot Does

TestPilot accepts testing instructions written in natural language.

For example:

> "I want to test my user registration system. How easy is it for a new user to create an account?"

From that request, TestPilot creates a testing strategy and delegates the work to browser-controlling AI agents.

The workflow consists of three primary stages:

### 1. Plan

An **Orchestrator Agent** receives the user's high-level testing request and determines how the feature should be tested.

For example, a request to test a login system could result in tasks such as:

* Create a new account
* Log in with valid credentials
* Attempt to log in with an incorrect password
* Submit the form with missing fields
* Test the password recovery flow

The orchestrator determines how many agents are needed, what each agent should do, and how tasks should be grouped.

### 2. Execute

The orchestrator spawns **Executor Agents**, each of which receives a specific, focused task.

Instead of asking one agent to:

> "Test the entire login system."

an executor might receive:

> "Navigate to the signup page and create a new account using generated test credentials."

The executor uses a real browser to interact with the application and reports what happened.

Multiple executor agents can run independently, allowing different parts of an application to be explored in parallel.

### 3. Report

Once the executor agents finish, the orchestrator analyzes their results and generates a final testing report.

Reports can include:

* Issues discovered
* Successful tests
* Execution logs
* Screenshots
* Visual evidence of what occurred during testing

---

## Testing at Any Level of Specificity

TestPilot is designed to work whether you have a detailed testing plan or only a general idea of what you want to investigate.

### Vague request

> "I'm not sure how to test my checkout flow. Can you test it?"

TestPilot can determine what should be tested and create a plan.

### General goal

> "Test whether a new user can successfully create an account."

TestPilot can turn the goal into concrete test cases and execute them.

### Specific instructions

> "Go to the signup page, create an account with these credentials, and verify that the user is redirected to the dashboard."

TestPilot can carry out the specified task directly.

This flexibility makes TestPilot useful throughout the testing process—from determining what should be tested to actually executing the tests.

---

## Architecture

TestPilot is built around two major components:

1. **The Django web application**
2. **The agentic testing system**

### Django Web Application

The Django application serves as both the backend and frontend of TestPilot.

It is responsible for:

* Storing testing runs
* Tracking agent tasks and their results
* Storing screenshots
* Storing execution logs
* Managing the state of testing runs
* Presenting results to the user
* Providing the interface for submitting testing requests
* Displaying generated reports and evidence

### Agentic Testing System

The agentic portion of TestPilot contains the prompts, tools, and logic responsible for planning and executing tests.

Files pertaining to this can be found in the **base** folder.

The agents are built using the **AWS Strands Agents SDK** and have access to custom tools that allow them to interact with the Django application's data layer.

This allows agents to record progress, retrieve information, and save testing results.

---

## Agents

TestPilot separates agent responsibilities into two primary categories.

### Orchestrator Agents

Orchestrator Agents are responsible for the **planning and summarization** stages of testing.

They receive the user's high-level request and determine how it should be tested within the available constraints.

Their primary tool is:

```text
run_agentic_task()
```

This tool allows an orchestrator to spawn a set of Executor Agents, with each executor receiving a specific task.

An orchestrator can create multiple sets of agentic tasks as necessary, allowing the testing strategy to evolve as the application is explored.

Once the tasks are complete, the orchestrator collects the results and produces the final report.

### Executor Agents

Executor Agents are responsible for actually interacting with the website.

They receive a focused task from the orchestrator, such as:

> "Create a new account using a generated email address and password."

Each Executor Agent has access to a browser instance through the Strands SDK's local Chromium browser tool.

TestPilot also provides custom tools for generating testing data, including:

* Email addresses
* Usernames
* Passwords

This allows agents to interact with applications that require account creation without requiring developers to manually provide test data.

### Why Separate the Agents?

Separating planning from execution allows each agent to focus on a smaller responsibility.

Rather than asking one large agent to plan, browse, reason, and execute an entire testing strategy, TestPilot breaks the workflow into smaller tasks.

This can make agents more focused and reliable while also helping control token usage, since complex browser-based tasks can become expensive when handled by a single agent.

---

## End-to-End Workflow

At a high level, TestPilot follows this workflow:

```text
User Request
     │
     ▼
Orchestrator Agent
     │
     ▼
Test Plan
     │
     ▼
Executor Agents
     │
     ▼
Browser Interaction
     │
     ▼
Execution Results
     │
     ▼
Orchestrator Analysis
     │
     ▼
Final Report
```

The complete product workflow is:

**User request → Test plan → Agentic tasks → Browser interaction → Results → Final report**

---

## Screenshot & Evidence Management

Screenshots are an important part of browser-based testing because they provide visual evidence of what an agent encountered.

A textual execution log might indicate that a button failed, while a screenshot can show exactly what the user would have seen.

The Strands SDK's local Chromium browser tool saves screenshots to a predefined local directory by default. TestPilot needs to associate screenshots with the correct testing run and executor task.

To address this, TestPilot uses a customized subclass of the local Chromium browser implementation that overrides the default screenshot storage behavior.

This file is called `scoped_browser.py`.

This allows screenshots to be stored and associated with the appropriate TestPilot run.

---

## Getting Started

### Prerequisites

Before running TestPilot locally, make sure you have:

* Git
* Python **3.13.5**
* Access to the Inference API of your choice

---

## Installation

### 1. Clone the repository

Clone the repository and navigate into the project directory:

```bash
git clone https://github.com/purplebearr/TestPilot.git
cd TestPilot
```

### 2. Create a Python 3.13 virtual environment

Create a new virtual environment using Python 3.13:

```bash
python3.13 -m venv .venv
```

Activate the virtual environment.

**macOS / Linux:**

```bash
source .venv/bin/activate
```

**Windows (PowerShell):**

```powershell
.venv\Scripts\Activate.ps1
```

You should see `(.venv)` in your terminal prompt after activation.

### 3. Install dependencies

With the virtual environment activated, install the project's Python dependencies:

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Create or populate the project's `.env` file with the environment variables required by the application.

Make sure all required values are populated before starting the application.

---

## Running TestPilot

TestPilot requires both the Django development server and the Huey task worker to be running.

Open **two terminal windows** and activate the virtual environment in both.

### Terminal 1 — Django Server

```bash
python manage.py migrate
```

Migrate changes to the database.

```bash
python manage.py runserver
```

This starts the TestPilot web application.

### Terminal 2 — Huey Worker

In a second terminal, start the Huey worker:

```bash
python manage.py run_huey
```

Keep both terminals running while using TestPilot.

Your local development environment should therefore have:

```text
Terminal 1
──────────
python manage.py runserver

Terminal 2
──────────
python manage.py run_huey
```

The Django server handles the web application, while the Huey process handles background tasks required by the application.

---

## Using TestPilot

Once the application is running, open the Django application in your browser at [http://127.0.0.1:8000](http://127.0.0.1:8000)

From there, create a new user account, then navigate to the dashboard. Click "New Run", and choose whether to provide your own prompt or use the prompt builder.

---