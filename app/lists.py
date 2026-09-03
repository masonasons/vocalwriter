#!/usr/bin/env python3
"""A report list a screen reader can actually read.

`wx.ListCtrl` in report mode is a native control on Windows and is read
correctly there. On macOS it is not native: wxWidgets draws it itself, and what
reaches the accessibility API is an empty box. Asking the system what is inside
each of the two, side by side in the same window:

    DataViewListCtrl   AXOutline   5 children
    wx.ListCtrl        AXGroup     0 children

So VoiceOver has nothing to read -- not the tracks, not the notes, not the bend
points. `wx.dataview.DataViewListCtrl` is backed by a real NSOutlineView and is
read properly, so it is used instead wherever wx.ListCtrl is not native.

This class is the small part of the wx.ListCtrl interface the editor actually
uses, over whichever of the two is right for the platform, so nothing above it
has to care.
"""
import wx
import wx.dataview as dv

#: wx.ListCtrl is native, and accessible, on Windows only.
NATIVE_LISTCTRL = wx.Platform == '__WXMSW__'


if NATIVE_LISTCTRL:

    class ReportList(wx.ListCtrl):
        """wx.ListCtrl unchanged, which is what Windows should keep using."""

        def __init__(self, parent, style=wx.LC_REPORT, size=wx.DefaultSize):
            wx.ListCtrl.__init__(self, parent, style=style, size=size)

        def SelectedRows(self):
            rows, i = [], self.GetFirstSelected()
            while i != -1:
                rows.append(i)
                i = self.GetNextSelected(i)
            return rows

else:

    class ReportList(dv.DataViewListCtrl):
        """The same interface over a control macOS exposes to VoiceOver."""

        def __init__(self, parent, style=wx.LC_REPORT, size=wx.DefaultSize):
            multiple = not (style & wx.LC_SINGLE_SEL)
            dv.DataViewListCtrl.__init__(
                self, parent, size=size,
                style=dv.DV_ROW_LINES |
                (dv.DV_MULTIPLE if multiple else dv.DV_SINGLE))
            self._cols = 0

        # -- columns and rows ------------------------------------------------

        def InsertColumn(self, index, heading, width=-1):
            self.AppendTextColumn(heading, width=width if width > 0 else -1)
            self._cols += 1
            return index

        def InsertItem(self, index, label=''):
            row = [label] + [''] * max(self._cols - 1, 0)
            if index >= self.GetItemCount():
                self.AppendItem(row)
            else:
                dv.DataViewListCtrl.InsertItem(self, index, row)
            return index

        def SetItem(self, index, column, label, imageId=-1):
            self.SetTextValue(str(label), index, column)

        # -- selection -------------------------------------------------------
        #
        # The editor drives everything from the keyboard, so where the
        # selection is *and* where focus sits both matter: selecting a row
        # without making it current leaves the next arrow key starting from
        # wherever the control was before.

        def Select(self, index, on=1):
            if 0 <= index < self.GetItemCount():
                if on:
                    self.SelectRow(index)
                else:
                    self.UnselectRow(index)

        def Focus(self, index):
            if 0 <= index < self.GetItemCount():
                item = self.RowToItem(index)
                self.EnsureVisible(item)
                self.SetCurrentItem(item)

        def SelectedRows(self):
            rows = []
            for item in self.GetSelections():
                r = self.ItemToRow(item)
                if r != wx.NOT_FOUND:
                    rows.append(r)
            return sorted(rows)

        def GetFirstSelected(self):
            rows = self.SelectedRows()
            return rows[0] if rows else -1

        def GetNextSelected(self, after):
            for r in self.SelectedRows():
                if r > after:
                    return r
            return -1

        def GetSelectedItemCount(self):
            return len(self.SelectedRows())

        # -- events ----------------------------------------------------------

        def Bind(self, event, handler=None, *args, **kw):
            if event is wx.EVT_LIST_ITEM_ACTIVATED:
                event = dv.EVT_DATAVIEW_ITEM_ACTIVATED
            elif event is wx.EVT_LIST_ITEM_SELECTED:
                event = dv.EVT_DATAVIEW_SELECTION_CHANGED
            elif event is wx.EVT_LIST_ITEM_DESELECTED:
                event = dv.EVT_DATAVIEW_SELECTION_CHANGED
            return super(ReportList, self).Bind(event, handler, *args, **kw)
