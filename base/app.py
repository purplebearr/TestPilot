from flask import Flask, request, render_template_string

app = Flask(__name__)

HTML = """
<!doctype html>
<html>
<head>
    <title>Create Account</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            max-width: 500px;
            margin: 60px auto;
            padding: 20px;
        }

        label {
            display: block;
            margin-top: 15px;
            margin-bottom: 5px;
        }

        input {
            width: 100%;
            box-sizing: border-box;
            padding: 10px;
            font-size: 16px;
        }

        button {
            margin-top: 20px;
            padding: 10px 20px;
            font-size: 16px;
            cursor: pointer;
        }

        .error {
            color: #b00020;
            margin: 15px 0;
        }

        .success {
            color: #087f23;
            margin: 30px 0;
            font-size: 20px;
        }
    </style>
</head>
<body>
    {% if success %}
        <h1>Account Created</h1>
        <div class="success" id="success-message">
            Success! Your account has been created.
        </div>
        <a href="/">Create another account</a>
    {% else %}
        <h1>Create Account</h1>

        {% if error %}
            <div class="error" id="error-message">{{ error }}</div>
        {% endif %}

        <form method="POST">
            <label for="username">Username</label>
            <input
                type="text"
                id="username"
                name="username"
                value="{{ username }}"
                required
                minlength="3"
            >

            <label for="email">Email</label>
            <input
                type="email"
                id="email"
                name="email"
                value="{{ email }}"
                required
            >

            <label for="password">Password</label>
            <input
                type="password"
                id="password"
                name="password"
                required
                minlength="8"
            >

            <label for="confirm_password">Confirm Password</label>
            <input
                type="password"
                id="confirm_password"
                name="confirm_password"
                required
                minlength="8"
            >

            <button type="submit" id="create-account">
                Create Account
            </button>
        </form>
    {% endif %}
</body>
</html>
"""


@app.route("/", methods=["GET", "POST"])
def create_account():
    if request.method == "GET":
        return render_template_string(
            HTML,
            success=False,
            error=None,
            username="",
            email=""
        )

    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")

    if len(username) < 3:
        error = "Username must be at least 3 characters."
    elif "@" not in email or "." not in email:
        error = "Please enter a valid email address."
    elif len(password) < 8:
        error = "Password must be at least 8 characters."
    elif password != confirm_password:
        error = "Passwords do not match."
    else:
        return render_template_string(
            HTML,
            success=True,
            error=None,
            username=username,
            email=email
        )

    return render_template_string(
        HTML,
        success=False,
        error=error,
        username=username,
        email=email
    )


if __name__ == "__main__":
    app.run(debug=True, threaded=True)