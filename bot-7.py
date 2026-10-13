# bot.py — entry point. Loads the pieces below, in order, into ONE shared namespace.
# (Same behaviour as the old single file; "global" variables keep working.)
import os as _os
_HERE = _os.path.dirname(_os.path.abspath(__file__))
_PIECES = ["core", "launchpad", "dropped_call", "x_alert", "leaderboard", "commands", "runtime"]
for _name in _PIECES:
    _path = _os.path.join(_HERE, _name + ".py")
    with open(_path, encoding="utf-8") as _f:
        exec(compile(_f.read(), _path, "exec"), globals())

if __name__ == "__main__":
    main()
