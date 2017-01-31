# Copyright (c) 2014-2016 Cedric Bellegarde <cedric.bellegarde@adishatz.org>
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

from gi.repository import Gtk, Gdk

from eolie.define import El
from eolie.popover_uri import UriPopover


class ToolbarTitle(Gtk.Bin):
    """
        Title toolbar
    """

    def __init__(self):
        """
            Init toolbar
        """
        Gtk.Bin.__init__(self)
        self.__uri = ""
        self.__lock = False
        self.__signal_id = None
        builder = Gtk.Builder()
        builder.add_from_resource('/org/gnome/Eolie/ToolbarTitle.ui')
        builder.connect_signals(self)
        self.__entry = builder.get_object('entry')
        self.__popover = UriPopover()
        self.__reload_image = builder.get_object('reload_image')
        self.add(builder.get_object('widget'))

    def set_width(self, width):
        """
            Set Gtk.Scale progress width
            @param width as int
        """
        self.set_property("width_request", width)

    def set_uri(self, uri):
        """
            Update entry
            @param text as str
        """
        if uri is not None:
            self.__uri = uri
            self.__entry.set_text(uri)
            self.__entry.set_placeholder_text("")
            self.__entry.get_style_context().remove_class('uribar-title')

    def set_title(self, title):
        """
            Show title instead of uri
        """
        if title is not None:
            self.__entry.set_placeholder_text(title)
            if not self.__lock:
                self.__entry.set_text("")
                self.__entry.get_style_context().add_class('uribar-title')

#######################
# PROTECTED           #
#######################
    def _on_enter_notify(self, eventbox, event):
        """
            Show uri
            @param eventbox as Gtk.EventBox
            @param event as Gdk.Event
        """
        current_text = self.__entry.get_text()
        if current_text == "":
            self.__entry.set_text(self.__uri)
            self.__entry.get_style_context().remove_class('uribar-title')

    def _on_leave_notify(self, eventbox, event):
        """
            Show uri
            @param eventbox as Gtk.EventBox
            @param event as Gdk.Event
        """
        allocation = eventbox.get_allocation()
        if event.x <= 0 or\
           event.x >= allocation.width or\
           event.y <= 0 or\
           event.y >= allocation.height:
            if self.__entry.get_placeholder_text() and\
                    self.__entry.get_text() and\
                    not self.__lock:
                self.__entry.set_text("")
                self.__entry.get_style_context().add_class('uribar-title')

    def _on_entry_focus_in(self, entry, event):
        """
            Block entry on uri
            @param entry as Gtk.Entry
            @param event as Gdk.Event
        """
        self.__lock = True
        self.__entry.set_text(self.__uri)
        self.__entry.get_style_context().remove_class('uribar-title')
        self.__popover.set_relative_to(self)
        self.__popover.show()
        self.__popover.set_history_text("")
        self.__signal_id = self.__entry.connect('changed',
                                                self.__on_entry_changed)

    def _on_entry_focus_out(self, entry, event):
        """
            Show title
            @param entry as Gtk.Entry
            @param event as Gdk.Event
        """
        self.__lock = False
        if self.__signal_id is not None:
            self.__entry.disconnect(self.__signal_id)
            self.__signal_id = None
        if self.__entry.get_placeholder_text():
            self.__entry.set_text("")
            self.__entry.get_style_context().add_class('uribar-title')
            self.__popover.hide()

    def _on_key_press_event(self, entry, event):
        """
            Forward to popover history listbox if needed
            @param entry as Gtk.Entry
            @param event as Gdk.Event
        """
        if event.keyval in [Gdk.KEY_Down, Gdk.KEY_Up]:
            self.__popover.send_event_to_history(event)
            return True
        elif event.keyval == Gdk.KEY_Return and entry.get_text() == self.__uri:
            self.__popover.send_event_to_history(event)
            return True

    def _on_reload_press(self, eventbox, event):
        """
            Reload current view
            @param eventbox as Gtk.EventBox
            @param event as Gdk.Event
        """
        El().window.container.current.reload()

    def _on_reload_enter_notify(self, eventbox, event):
        """
            Change opacity
            @param eventbox as Gtk.EventBox
            @param event as Gdk.Event
        """
        self.__reload_image.set_opacity(1)

    def _on_reload_leave_notify(self, eventbox, event):
        """
            Change opacity
            @param eventbox as Gtk.EventBox
            @param event as Gdk.Event
        """
        self.__reload_image.set_opacity(0.8)

    def _on_activate(self, entry):
        """
            Go to url or search for words
            @param entry as Gtk.Entry
        """
        text = entry.get_text()
        El().window.container.load_uri(text)

#######################
# PRIVATE             #
#######################
    def __on_entry_changed(self, entry):
        """
            Update popover search if needed
        """
        value = entry.get_text()
        if value == self.__uri:
            self.__popover.set_history_text("")
        else:
            self.__popover.set_history_text(value)