#!/usr/bin/python -tt
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Library General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.

# Authors:
#    Tim Lauridsen <tla@rasmil.dk>

import os
import gtk
import gobject
from sulfur.setup import const, SulfurConf
import vte

def hex2float(myhex):
     ret = []
     for i in range(4):
             ic = int(myhex[i])
             ret.append( ic / 255.0 )
     return ret

class CellRendererStars(gtk.GenericCellRenderer):
    __gproperties__ = {
            "custom": (gobject.TYPE_OBJECT, "Custom",
            "Custom", gobject.PARAM_READWRITE),
    }

    def __init__(self):
        self.__gobject_init__()
        self.value = -1
        self.value_voted = 0.0

    def do_set_property(self, pspec, value):
        setattr(self, pspec.name, value)

    def do_get_property(self, pspec):
        return getattr(self, pspec.name)

    def on_render(self, window, widget, background_area, cell_area,
            expose_area, flags):

        (x_offset, y_offset, width, height) = self.on_get_size(widget,
            cell_area)
        if isinstance(window,gtk.gdk.Window):
            widget.style.paint_box(window,
                                gtk.STATE_NORMAL,
                                gtk.SHADOW_IN,
                                None, widget, "trough",
                                cell_area.x+x_offset,
                                cell_area.y+y_offset,
                                width, height)
        if ((self.value > -1) and (self.value < 6)) or (self.value_voted > 0):

            xt = widget.style.xthickness
            empty = gtk.Image()
            empty.set_from_file(const.empty_background)
            empty_buf = empty.get_pixbuf()

            if self.value_voted:
                star = gtk.Image()
                star.set_from_file(const.star_selected_pixmap)
            else:
                star = gtk.Image()
                star.set_from_file(const.star_normal_pixmap)

            star_empty = gtk.Image()
            star_empty.set_from_file(const.star_empty_pixmap)

            star_half = gtk.Image()
            star_half.set_from_file(const.star_half_pixmap)

            star_buf = star.get_pixbuf()
            star_empty_buf = star_empty.get_pixbuf()
            star_half_buf = star_half.get_pixbuf()

            w, h = star_buf.get_width(),star_buf.get_height()
            myval = self.value
            if self.value_voted:
                myval = self.value_voted
            empty_buf = empty_buf.scale_simple(w*5,h+12,gtk.gdk.INTERP_BILINEAR)
            myvals = [0,w,w*2,w*3,w*4]
            cnt = 0
            while myval > 0:
                if (myval < 0.6):
                    star_half_buf.copy_area(0, 0, w, h, empty_buf, myvals[cnt],
                        6)
                else:
                    star_buf.copy_area(0, 0, w, h, empty_buf, myvals[cnt], 6)
                myval -= 1
                cnt += 1
            myval = 5 - cnt
            while myval > 0:
                star_empty_buf.copy_area(0, 0, w, h, empty_buf, myvals[cnt], 6)
                myval -= 1
                cnt += 1

            if empty_buf: window.draw_pixbuf(None, empty_buf, 0, 0,
                cell_area.x+x_offset+xt, cell_area.y+y_offset+xt, -1, -1)


    def on_get_size(self, widget, cell_area):
        xpad = self.get_property("xpad")
        ypad = self.get_property("ypad")
        if cell_area:
            width = cell_area.width
            height = cell_area.height
            x_offset = xpad
            y_offset = ypad
        else:
            width = self.get_property("width")
            height = self.get_property("height")
            if width == -1: width = 100
            if height == -1: height = 30
            width += xpad*2
            height += ypad*2
            x_offset = 0
            y_offset = 0
        return x_offset, y_offset, width, height


gobject.type_register(CellRendererStars)


class SulfurConsole(vte.Terminal):

    def __init__(self):
        vte.Terminal.__init__(self)
        self.reset()

    def _dosettings(self):
        imgpath = os.path.join(const.PIXMAPS_PATH,
            'sabayon-console-background.png')
        if os.path.isfile(imgpath):
            self.set_background_image_file(imgpath)
        self.set_background_saturation(0.4)
        self.set_opacity(65535)
        myfc = gtk.gdk.color_parse(SulfurConf.color_console_font)
        self.set_color_foreground(myfc)

    def reset (self):
        vte.Terminal.reset(self, True, True)
        self._dosettings()








