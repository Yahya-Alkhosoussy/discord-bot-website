import os
import secrets
from functools import wraps

import requests
from dotenv import load_dotenv
from flask import Flask, abort, redirect, render_template, request, session, url_for

from database_interaction import add_role, get_react_roles_internal
from twitch_database_interaction import (
    add_bot_commands,
    add_user,
    change_activity,
    delete_command,
    edit_specific_command,
    get_bot_commands,
    get_specific_command,
    get_token_for_channel,
    get_user,
    is_user_in,
    save_twitch_token,
)

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")

DISCORD_API = "https://discord.com/api"
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")
REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")
TWITCH_REDIRECT_URI = "https://spider-byte.com/auth/twitch/callback"
BOT_TOKENS: list[str] = []
TOKEN_NAMES = ["SHARK_BOT_TOKEN", "SHARK_TEST_BOT_TOKEN", "NOTTSAIR_BOT_TEST"]
BOT_NAMES = ["SHARK_BOT", "SHARK_TEST_BOT", "NOTTSAIR_TEST_BOT"]
GUILD_NAMES = ["NottsAIR", "⊹˚₊⟡₊‧⁺˖ The Cult of Shark ˖⁺‧₊⟡₊˚⊹", "test server", "fruity server"]
for name in TOKEN_NAMES:
    token = os.getenv(name)
    if token:
        BOT_TOKENS.append(token)


BOT_INFO: dict[str, dict] = {}  # name -> user object


def load_bot_info():
    for bot_token, name in zip(BOT_TOKENS, BOT_NAMES):
        r = requests.get(
            f"{DISCORD_API}/users/@me",
            headers={"Authorization": f"Bot {bot_token}"},
        )
        if r.status_code == 200:
            BOT_INFO[name] = r.json()
        else:
            print(f"Warning: failed to load bot info for {name}: {r.status_code}")


def discord_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("discord_login"))
        return f(*args, **kwargs)

    return decorated


def discord_verification(guild_id):
    # confirm the user actually has manager permissions
    guilds = get_mod_guilds()
    if guilds is None:
        abort(403, "You do not manage any guilds")
    guild = next((g for g in guilds if g["id"] == guild_id), None)
    if guild is None:
        abort(403, "You don't have permission to manage this server.")

    return guild


load_bot_info()

app.jinja_env.globals["BOT_INFO"] = BOT_INFO


@app.route("/")
def index():
    return render_template("login.html")


@app.route("/login")
def discord_login():
    return redirect(
        "https://discord.com/oauth2/authorize"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        "&response_type=code"
        "&scope=identify+guilds"
    )


@app.route("/callback")
def callback():
    code = request.args.get("code")

    # exchange code for access token
    token_response = requests.post(
        f"{DISCORD_API}/oauth2/token",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    if "access_token" not in token_response.json():
        return redirect(url_for("index"))

    token = token_response.json()["access_token"]

    user = requests.get(f"{DISCORD_API}/users/@me", headers={"Authorization": f"Bearer {token}"}).json()

    # Store in session
    session["user"] = user
    session["token"] = token
    for token, name in zip(BOT_TOKENS, TOKEN_NAMES):
        bot = requests.get(f"{DISCORD_API}/users/@me", headers={"Authorization": f"Bot {token}"}).json()
        session[name] = bot

    return redirect(url_for("discord_dashboard"))


@app.route("/twitch_login")
def twitch_login():
    state = secrets.token_urlsafe(32)
    session["twitch_oauth_state"] = state
    return redirect(
        "https://id.twitch.tv/oauth2/authorize"
        f"?client_id={TWITCH_CLIENT_ID}"
        f"&redirect_uri={TWITCH_REDIRECT_URI}"
        "&response_type=code"
        "&scope=user:read:email+user:read:moderated_channels"
        f"&state={state}"
    )


async def refresh_twitch_token(broadcaster_login: str) -> bool:
    broadcaster = await get_user(broadcaster_login)
    if not broadcaster or not broadcaster.get("refresh_token"):
        return False

    resp = requests.post(
        "https://id.twitch.tv/oauth2/token",
        data={
            "client_id": TWITCH_CLIENT_ID,
            "client_secret": TWITCH_CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": broadcaster["refresh_token"],
        },
    ).json()

    if "access_token" not in resp:
        return False

    await save_twitch_token(
        login=broadcaster_login,
        user_id=broadcaster["twitch_id"],
        access_token=resp["access_token"],
        refresh_token=resp.get("refresh_token", broadcaster["refresh_token"]),
    )
    return True


async def is_twitch_moderator(channel_login: str, candidate_twitch_id: str) -> bool:
    broadcaster = await get_token_for_channel(channel_login)
    if broadcaster is None:
        return False

    r = requests.get(
        "https://api.twitch.tv/helix/moderation/moderators",
        params={"broadcaster_id": broadcaster["user_id"], "user_id": candidate_twitch_id},
        headers={"Authorization": f"Bearer {broadcaster['access_token']}", "Client-Id": TWITCH_CLIENT_ID},
    )
    if r.status_code == 401:
        refreshed = await refresh_twitch_token(channel_login)
        if not refreshed:
            return False
        broadcaster = await get_token_for_channel(channel_login)
        if broadcaster is None:
            return False

        r = requests.get(
            "https://api.twitch.tv/helix/moderation/moderators",
            params={
                "broadcaster_id": broadcaster["twitch_id"],
                "user_id": candidate_twitch_id,
            },
            headers={"Authorization": f"Bearer {broadcaster['access_token']}", "Client-Id": TWITCH_CLIENT_ID},
        )
    return len(r.json().get("data", [])) > 0


@app.route("/auth/twitch/callback")
async def twitch_callback():
    code = request.args.get("code")

    # exchange code for acces token
    token_response = requests.post(
        "https://id.twitch.tv/oauth2/token",
        data={
            "client_id": TWITCH_CLIENT_ID,
            "client_secret": TWITCH_CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": TWITCH_REDIRECT_URI,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    token_data = token_response.json()
    if "access_token" not in token_data:
        return redirect(url_for("index"))

    token = token_data["access_token"]
    user_response = requests.get(
        "https://api.twitch.tv/helix/users",
        headers={
            "Authorization": f"Bearer {token}",
            "Client-Id": TWITCH_CLIENT_ID,
        },
    ).json()

    user = user_response["data"][0]

    moderated = requests.get(
        "https://api.twitch.tv/helix/moderation/channels",
        params={"user_id": user["id"]},
        headers={"Authorization": f"Bearer {token}", "Client-Id": TWITCH_CLIENT_ID},
    ).json()

    moderated_channels = [
        {"id": ch["broadcaster_id"], "login": ch["broadcaster_login"], "name": ch["broadcaster_name"]}
        for ch in moderated.get("data", [])
    ]

    moderated_channels.append({"id": user["id"], "login": user["login"], "name": user["display_name"]})

    session["twitch_user"] = user
    session["twitch_token"] = token
    session["twitch_moderated_channels"] = moderated_channels

    if not await is_user_in(user["id"]) and token is not None:
        await add_user(user["login"], user["id"], token, user_response.get("refresh_token"))
    else:
        await save_twitch_token(user["login"], user["id"], token, user_response.get("refresh_token"))
    return redirect(url_for("twitch_dashboard"))


# Filter to only servers where they have manage guild or administrator
MANAGE_GUILD = 0x20
ADMINISTRATOR = 0x8


@app.route("/twitch/logout")
async def twitch_logout():
    # Revoke the token with twitch
    token = session.get("twitch_token")
    if token:
        requests.post(
            "https://id.twitch.tv/oauth2/revoke",
            params={
                "client_id": TWITCH_CLIENT_ID,
                "token": token,
            },
        )
    # clear twitch-related keys
    session.pop("twitch_user", None)
    session.pop("twitch_token", None)
    session.pop("twitch_oauth_state", None)
    print("Removed twitch related settings")
    return redirect(url_for("index"))


def get_mod_guilds():
    """Returns a list of guilds the user can manage, or None if the auth failed"""
    if "user" not in session or "token" not in session:
        return None
    guilds = requests.get(f"{DISCORD_API}/users/@me/guilds", headers={"Authorization": f"Bearer {session['token']}"}).json()
    if not isinstance(guilds, list):
        return None
    return [g for g in guilds if (int(g["permissions"]) & MANAGE_GUILD) or (int(g["permissions"]) & ADMINISTRATOR)]


def get_bot_guilds_names() -> dict:
    bot_guilds_dict = {}
    for token, name in zip(BOT_TOKENS, GUILD_NAMES):
        r = requests.get(f"{DISCORD_API}/users/@me/guilds", headers={"Authorization": f"Bot {token}"})
        guilds = r.json() if r.status_code == 200 else []
        bot_guilds_dict[name] = {guild["id"] for guild in guilds}
    return bot_guilds_dict


def get_bot_guilds():
    bot_guilds_dict = {}
    for token, name in zip(BOT_TOKENS, BOT_NAMES):
        r = requests.get(f"{DISCORD_API}/users/@me/guilds", headers={"Authorization": f"Bot {token}"})
        guilds = r.json() if r.status_code == 200 else []
        bot_guilds_dict[name] = {guild["id"] for guild in guilds}
    return bot_guilds_dict


def twitch_mod_required(f):
    @wraps(f)
    async def decorated(*args, **kwargs):
        bot_name = kwargs.get("bot_name")

        if "twitch_user" not in session:
            return redirect(url_for("twitch_login"))
        if bot_name is None:
            return redirect(url_for("twitch_dashboard"))

        channel_login = kwargs.get("channel_login")
        twitch_user = session["twitch_user"]
        if not channel_login or not await is_twitch_moderator(channel_login, twitch_user["id"]):
            return "Not a moderator for this channel.", 403
        return await f(*args, **kwargs)

    return decorated


@app.route("/twitch/dashboard")
async def twitch_dashboard():
    if "twitch_user" not in session:
        return redirect(url_for("twitch_login"))
    user = session["twitch_user"]
    profile_image = user["profile_image_url"]

    return render_template("twitch_dashboard.html", user=user, profile_image=profile_image)


@app.route("/twitch/dashboard/<channel_login>")
async def twitch_channel_dashboard(channel_login):
    if "twitch_user" not in session:
        return redirect(url_for("twitch_login"))
    user = session["twitch_user"]
    profile_image = user["profile_image_url"]
    command_details = await get_bot_commands(bot_id=1, streamer_login=channel_login)

    return render_template(
        "twitch/commands_dashboard.html",
        channel_login=channel_login,
        command_details=command_details,
        profile_image=profile_image,
    )


@app.route("/discord-dashboard")
@discord_login_required
def discord_dashboard():
    user = session["user"]

    mod_guilds = get_mod_guilds()

    bot_guilds = get_bot_guilds_names()
    assert mod_guilds
    guilds_to_show = []
    for guild in mod_guilds:
        if bot_guilds.get(guild["name"]):
            guilds_to_show.append(guild)

    return render_template("dashboard.html", user=user, guilds=guilds_to_show)


@app.route("/discord-dashboard/<guild_id>")
@discord_login_required
def manage_guild(guild_id):
    guild = discord_verification(guild_id)

    bots_in_guild: list[str] = []

    for bot_token, name in zip(BOT_TOKENS, BOT_NAMES):
        r = requests.get(
            f"{DISCORD_API}/guilds/{guild_id}",
            headers={"Authorization": f"Bot {bot_token}"},
        )
        if r.status_code == 200:
            bots_in_guild.append(name)

    guild["bots_present"] = bots_in_guild

    if not bots_in_guild:
        return "No bot is in this server.", 404
    if len(bots_in_guild) == 1:
        return redirect(url_for("manage_guild_bot", guild_id=guild_id, guild=guild, bot_name=bots_in_guild[0]))

    return render_template(
        "dashboard/choose_bot.html",
        guild=guild,
        bots=bots_in_guild,
        user=session["user"],
    )


@app.route("/discord-dashboard/<guild_id>/<bot_name>")
@discord_login_required
def manage_guild_bot(guild_id, bot_name):
    # auth
    # confirm the user actually has manager permissions
    guild = discord_verification(guild_id)
    if bot_name not in BOT_NAMES:
        return "Unknown bot", 404
    bot_guilds = get_bot_guilds()
    if guild_id not in bot_guilds.get(bot_name, set()):
        return "This bot is not in this server", 404

    active_tab = request.args.get("tab", "general")

    return render_template(
        "dashboard/manage_guild.html",
        guild=guild,
        user=session["user"],
        bot_name=bot_name,
        bots=BOT_NAMES,
        active_tab=active_tab,
    )


@app.route("/discord-dashboard/<guild_id>/<bot_name>/react-roles")
@discord_login_required
def manage_react_roles(guild_id, bot_name):
    guild = discord_verification(guild_id)

    role_mapping = get_react_roles_internal(int(guild_id))
    assert role_mapping is not None
    role_mapping = role_mapping.get(int(guild_id))

    return render_template(
        "dashboard/manage_react_roles.html",
        guild=guild,
        user=session["user"],
        bot_name=bot_name,
        role_mapping=role_mapping,  # dict[int, dict[str, dict[str, tuple[int, str]]]]
    )


def get_guild_roles(guild_id: int):
    for bot_token in BOT_TOKENS:
        r = requests.get(f"{DISCORD_API}/guilds/{guild_id}/roles", headers={"Authorization": f"Bot {bot_token}"})
        if r.status_code == 200:
            return r.json()
    return []


@app.route("/twitch/dashboard/<channel_login>/add_command", methods=["GET", "POST"])
async def add_command(channel_login):
    if "twitch_user" not in session:
        return redirect(url_for("twitch_login"))
    user = session["twitch_user"]
    if not await get_user(user["login"]):
        await add_user(
            username=user["login"], user_id=user["id"], access_token=session["twitch_token"], bot_id=1, refresh_token=None
        )
    profile_image = user["profile_image_url"]
    if request.method == "GET":
        return render_template(
            "dashboard/add_command_twitch.html",
            profile_image=profile_image,
            channel_login=channel_login,
        )
    # POST SO PROCESS
    name = request.form.get("name", "")
    Reply = request.form.get("reply", "")
    user_lvl = request.form.get("user_lvl", "")
    success = await add_bot_commands(name, Reply, user_lvl, channel_login, 1)
    if success:
        return redirect(url_for("twitch_channel_dashboard", channel_login=channel_login))
    else:
        return render_template(
            "dashboard/add_command_twitch.html",
            profile_image=profile_image,
        )


@app.route("/twitch/dashboard/toggle-command/<command_id>", methods=["POST"])
async def toggle_command(command_id):
    if "twitch_user" not in session:
        return {"Error": "Unauthorized"}, 401

    success = await change_activity(command_id)
    if success:
        return {"ok": True}, 200
    return {"error": "Failed to toggle"}, 500


@app.route("/twitch/dashboard/delete-command/<command_id>", methods=["POST"])
async def del_command(command_id):
    if "twitch_user" not in session:
        return {"Error": "Unauthorized"}, 401

    success = await delete_command(command_id)
    if success:
        return {"ok": True}, 200
    return {"error": "Failed to delete"}, 500


@app.route("/twitch/dashboard/<channel_login>/edit_command/<command_id>", methods=["GET", "POST"])
async def edit_command(channel_login, command_id):
    if "twitch_user" not in session:
        return redirect(url_for("twitch_login"))
    user = session["twitch_user"]
    if not await get_user(user["login"]):
        await add_user(username=user["login"], user_id=user["id"], access_token=session["twitch_token"], refresh_token=None)
    profile_image = user["profile_image_url"]
    details = await get_specific_command(streamer_name=channel_login, command_id=command_id)
    if request.method == "GET":
        if details is None:
            return "Command not found", 400
        user_levels = ["Everyone", "Subscriber", "VIP", "Moderator", "Broadcaster"]
        return render_template(
            "dashboard/edit_command_twitch.html",
            command_details=details,
            profile_image=profile_image,
            user_lvls=user_levels,
            command_id=command_id,
        )

    # POST so process
    name = request.form.get("name", "")
    Reply = request.form.get("reply", "")
    user_lvl = request.form.get("user_lvl", "")
    active = "active" in request.form
    success = await edit_specific_command(name, command_id, Reply, user_lvl, bool(active))
    if success:
        return redirect(url_for("twitch_channel_dashboard", channel_login=channel_login))
    else:
        return render_template(
            "dashboard/edit_command_twitch.html", profile_image=profile_image, command_id=command_id, command_details=details
        )


@app.route("/discord-dashboard/<guild_id>/<bot_name>/react-roles/add-role/<set_name>", methods=["GET", "POST"])
@discord_login_required
def add_react_role(guild_id, set_name, bot_name):
    guild = discord_verification(guild_id)

    if set_name is None:
        return "Missing set_name", 400

    if request.method == "GET":
        return render_template(
            "dashboard/add_react_role.html",
            guild=guild,
            user=session["user"],
            set_name=set_name,
            bot_name=bot_name,
            roles=get_guild_roles(guild_id),
        )

    # POST so process
    emoji = request.form.get("emoji", "").strip()
    role_id = request.form.get("role_id", "").strip()

    if not emoji or not role_id.isdigit():
        return render_template(
            "dashboard/add_react_role.html",
            guild=guild,
            set_name=set_name,
            bot_name=bot_name,
            roles=get_guild_roles(guild_id),
            error="All fields required; IDs must be numeric.",
        )

    guild_roles = get_guild_roles(guild_id)
    guild_role_id_to_name = {role["id"]: role for role in guild_roles}
    role = guild_role_id_to_name[role_id]
    success = False
    if role is not None:
        if emoji.startswith("<:"):
            animated = False
            emoji_details = emoji[2:-1].split(":", 1)
            emoji_name = emoji_details[0]
            emoji_id = int(emoji_details[1])
        elif emoji.startswith("<a:"):
            animated = True
            emoji_details = emoji[3:-1].split(":", 1)
            emoji_name = emoji_details[0]
            emoji_id = int(emoji_details[1])
        else:
            animated = True
            emoji_name = emoji
            emoji_id = None
        success = add_role(role["name"], int(role_id), emoji_name, animated, emoji_id, set_name, guild["name"], guild_id)

    if success:
        return redirect(url_for("manage_react_roles", guild_id=guild_id, bot_name=bot_name))
    return render_template(
        "dashboard/add_react_role.html",
        guild=guild,
        set_name=set_name,
        bot_name=bot_name,
        roles=get_guild_roles(guild_id),
        error="All fields required; IDs must be numeric.",
    )


@app.route("/discord-dashboard/<guild_id>/<bot_name>/react-roles/add-role-message", methods=["GET", "POST"])
@discord_login_required
def add_new_react_role_message(guild_id, bot_name):
    guild = discord_verification(guild_id)

    if request.method == "GET":
        return render_template(
            "dashboard/add_react_role_message.html",
            guild=guild,
            user=session["user"],
            bot_name=bot_name,
            roles=get_guild_roles(guild_id),
        )

    emoji = request.form.get("emoji", "").strip()
    role_id = request.form.get("role_id", "").strip()
    set_name = request.form.get("set_name", "")

    if not emoji or not role_id.isdigit():
        return render_template(
            "dashboard/add_react_role_message.html",
            guild=guild,
            bot_name=bot_name,
            roles=get_guild_roles(guild_id),
            error="All fields required; IDs must be numeric.",
        )

    guild_roles = get_guild_roles(guild_id)
    guild_role_id_to_name = {role["id"]: role for role in guild_roles}
    role = guild_role_id_to_name[role_id]
    success = False
    if role is not None:
        if emoji.startswith("<:"):
            animated = False
            emoji_details = emoji[2:-1].split(":", 1)
            emoji_name = emoji_details[0]
            emoji_id = int(emoji_details[1])
        elif emoji.startswith("<a:"):
            animated = True
            emoji_details = emoji[3:-1].split(":", 1)
            emoji_name = emoji_details[0]
            emoji_id = int(emoji_details[1])
        else:
            animated = True
            emoji_name = emoji
            emoji_id = None
        success = add_role(role["name"], int(role_id), emoji_name, animated, emoji_id, set_name, guild["name"], guild_id)

    if success:
        return redirect(url_for("manage_react_roles", guild_id=guild_id, bot_name=bot_name))
    return render_template(
        "dashboard/add_react_role_message.html",
        guild=guild,
        roles=get_guild_roles(guild_id),
        bot_name=bot_name,
        error="All fields required; IDs must be numeric.",
    )


if __name__ == "__main__":
    app.run(port=3000, host="0.0.0.0")
