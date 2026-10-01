import os
from functools import wraps

import requests
from flask import Blueprint, abort, redirect, render_template, request, session, url_for  # noqa

from database_interaction import add_role, get_react_roles_internal

discord_bp = Blueprint("discord", "discord", url_prefix="/bot-management/discord")

DISCORD_API = "https://discord.com/api"
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")

BOT_TOKENS: list[str] = []
TOKEN_NAMES = ["SHARK_BOT_TOKEN", "SHARK_TEST_BOT_TOKEN", "NOTTSAIR_BOT_TEST"]
BOT_NAMES = ["SHARK_BOT", "SHARK_TEST_BOT", "NOTTSAIR_TEST_BOT"]
GUILD_NAMES = ["NottsAIR", "⊹˚₊⟡₊‧⁺˖ The Cult of Shark ˖⁺‧₊⟡₊˚⊹", "test server", "fruity server"]
for name in TOKEN_NAMES:
    token = os.getenv(name)
    if token:
        BOT_TOKENS.append(token)


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


BOT_INFO: dict[str, dict] = {}  # name -> user object


@discord_bp.app_template_global(name="BOT_INFO")
def get_bot_info():
    return BOT_INFO


@discord_bp.route("/login")
def discord_login():
    return redirect(
        "https://discord.com/oauth2/authorize"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        "&response_type=code"
        "&scope=identify+guilds"
    )


@discord_bp.route("/callback")
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


# Filter to only servers where they have manage guild or administrator
MANAGE_GUILD = 0x20
ADMINISTRATOR = 0x8


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


@discord_bp.route("/discord-dashboard")
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


@discord_bp.route("/discord-dashboard/<guild_id>")
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


@discord_bp.route("/discord-dashboard/<guild_id>/<bot_name>")
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


@discord_bp.route("/discord-dashboard/<guild_id>/<bot_name>/react-roles")
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


@discord_bp.route("/discord-dashboard/<guild_id>/<bot_name>/react-roles/add-role/<set_name>", methods=["GET", "POST"])
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


@discord_bp.route("/discord-dashboard/<guild_id>/<bot_name>/react-roles/add-role-message", methods=["GET", "POST"])
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
