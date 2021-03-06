# Copyright (c) 2017 Cedric Bellegarde <cedric.bellegarde@adishatz.org>
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

from gi.repository import Gio

El = Gio.Application.get_default


class ArtSize:
    FAVICON = 22
    PREVIEW_HEIGHT = 60
    PREVIEW_WIDTH_MARGIN = 10
    START_WIDTH = 300
    START_HEIGHT = 200


class Type:
    NONE = -1
    POPULARS = -2
    RECENTS = -3
    BOOKMARK = -4
    KEYWORDS = -5
    HISTORY = -6
    SEARCH = -7
    TAG = -8
    SEPARATOR = -9


LOGINS = ["login", "username", "user", "mail", "email"]
PASSWORDS = ["password", "passwd", "pass"]
