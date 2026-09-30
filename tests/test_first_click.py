"""A click on a control in a scrolling page (the Settings tab) takes the first time.

The bug: a click gives the control it's on the focus, and Tk tells every widget between the page and the control
that the focus came in (FocusIn, detail NotifyVirtual) before the button comes up. The Settings page scrolled to
show each of them - the page's top, the card's top - so the control moved out from under the pointer, the button
came up somewhere else, and the click was lost (a second click worked: nothing moved then). On a virtual screen,
with real clicks, 64 of 88 first clicks on the Settings page were lost; none are now.

A hidden window can't take a real click, so these tests drive the page's window-wide bindings the way Tk does:
the button going down and up, and the FocusIn events with their detail. Everything is headless."""

import gc
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def hidden_root():
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()                       # headless: never shown
    return root


class FirstClickTest(unittest.TestCase):
    def setUp(self):
        try:
            self.root = hidden_root()
        except Exception as exc:          # noqa: BLE001
            self.skipTest(f"Tk unavailable: {exc}")
        from projectionist.ui import widgets
        self.W = widgets

    def tearDown(self):
        self.root.destroy()
        self.root = None
        gc.collect()

    def page(self, follow_focus=True, cards=8):
        """A Settings-like page: cards of radio buttons, much taller than its view, laid out though hidden."""
        import tkinter as tk
        from tkinter import ttk
        holder = tk.Frame(self.root)
        holder.place(x=0, y=0, width=500, height=300)
        sf = self.W.ScrollFrame(holder, follow_focus=follow_focus)
        sf.pack(fill="both", expand=True)
        self.var = tk.StringVar(self.root, "a")
        self.cards = []
        for n in range(cards):
            card = ttk.Frame(sf.inner, padding=10)
            card.pack(fill="x", pady=6)
            ttk.Label(card, text=f"Card {n}").pack(anchor="w")
            for value in ("a", "b", "c"):
                ttk.Radiobutton(card, text=value, value=f"{n}{value}", variable=self.var).pack(anchor="w")
            self.cards.append(card)
        self.root.update()
        return sf

    def radio(self, card: int, which: int = 1):
        return self.cards[card].winfo_children()[1 + which]

    def focus_in(self, widget, detail="NotifyAncestor"):
        widget.event_generate("<FocusIn>", detail=detail)
        self.root.update_idletasks()

    def test_the_widgets_on_the_way_to_the_focus_never_scroll_the_page(self):
        sf = self.page()
        sf.canvas.yview_moveto(0.5)
        self.root.update_idletasks()
        here = sf.canvas.yview()
        target = self.radio(5)
        # what Tk sends as the focus goes to a control in card 5: the page, the card, then the control
        for widget in (sf.inner, self.cards[5]):
            for detail in ("NotifyVirtual", "NotifyNonlinearVirtual"):
                self.focus_in(widget, detail)
                self.assertEqual(sf.canvas.yview(), here, (str(widget), detail))
        self.focus_in(target, "NotifyInferior")               # (Tk itself never passes these on)
        self.focus_in(target, "NotifyPointer")
        self.assertEqual(sf.canvas.yview(), here)

    def test_a_clicks_focus_never_scrolls_the_page(self):
        sf = self.page()
        sf.canvas.yview_moveto(0.5)
        self.root.update_idletasks()
        here = sf.canvas.yview()
        below = self.radio(7, 2)                              # (out of view: the keyboard's Tab would show it)
        below.event_generate("<ButtonPress-1>")               # the button goes down on it...
        self.assertTrue(self.W.pointer_down(below))
        self.focus_in(below)                                  # ...and the click gives it the focus
        self.focus_in(below, "NotifyNonlinear")
        self.assertEqual(sf.canvas.yview(), here)             # the page stays under the pointer
        below.event_generate("<ButtonRelease-1>")
        self.assertFalse(self.W.pointer_down(below))

    def test_coming_back_to_the_window_never_scrolls_the_page(self):
        """Back from another program - by a click, most likely: Tk gives the focus back to the widget that had it
        (a spin box, say, scrolled out of view since) before the click's press arrives."""
        sf = self.page()
        spun_away = self.radio(7, 2)
        self.root.event_generate("<FocusIn>", detail="NotifyNonlinearVirtual")    # (the window, on the way)
        self.focus_in(spun_away, "NotifyNonlinear")
        self.assertEqual(sf.canvas.yview()[0], 0.0)
        self.root.update()                                    # (that change of focus is over)
        self.focus_in(spun_away, "NotifyNonlinear")           # Tab to it later: shown
        self.assertGreater(sf.canvas.yview()[0], 0.5)

    def test_tab_to_a_control_out_of_view_still_shows_it(self):
        sf = self.page()
        self.assertEqual(sf.canvas.yview()[0], 0.0)
        last = self.radio(7, 2)
        self.focus_in(last, "NotifyNonlinear")                # Tab from a control in another card
        self.assertGreater(sf.canvas.yview()[0], 0.5)
        top = sf.canvas.canvasy(0)
        y = last.winfo_rooty() - sf.inner.winfo_rooty()
        self.assertTrue(top <= y and y + last.winfo_height() <= top + sf.canvas.winfo_height())
        self.focus_in(self.radio(0, 0))                       # Shift+Tab back up (from the page itself)
        self.assertLess(sf.canvas.yview()[0], 0.1)

    def test_only_a_page_that_follows_the_focus_scrolls_to_it(self):
        sf = self.page(follow_focus=False)
        self.focus_in(self.radio(7, 2), "NotifyNonlinear")
        self.assertEqual(sf.canvas.yview()[0], 0.0)

    def test_a_press_whose_release_never_came_is_forgotten(self):
        self.page()
        pointer = self.root.__dict__["_pointer"]
        pointer.update(down=True, at=time.monotonic())
        self.assertTrue(self.W.pointer_down(self.root))
        pointer["at"] = time.monotonic() - self.W.POINTER_HELD - 1       # (a dialog took the release)
        self.assertFalse(self.W.pointer_down(self.root))
        self.assertFalse(self.W.pointer_down(object()))                  # (not a widget: never down)

    def test_the_window_has_one_set_of_bindings_however_many_pages(self):
        """A binding per page stayed in Tk for good (bind_all), each keeping its page alive after it was gone -
        and a tab built again (a new text size) makes new pages."""
        self.page()
        scripts = {seq: self.root.bind_all(seq) for seq in ("<Button-1>", "<ButtonRelease-1>", "<MouseWheel>")}
        focus = self.root.tk.call("bind", "all", "<FocusIn>")
        pages = [self.W.ScrollFrame(self.root) for _ in range(5)]
        for seq, script in scripts.items():
            self.assertEqual(self.root.bind_all(seq), script, seq)
        self.assertEqual(self.root.tk.call("bind", "all", "<FocusIn>"), focus)
        self.assertEqual(self.W.scroll_pages(self.root)[1:], pages)                   # (in the order made)
        for p in pages:
            p.destroy()
        self.assertEqual(len(self.W.scroll_pages(self.root)), 1)
        del pages, p
        gc.collect()
        self.assertEqual(len(self.root.__dict__["_scroll_pages"]), 1)   # (let go of once gone)

    def test_a_click_still_gives_the_page_the_keys(self):
        sf = self.page()
        taken = []
        sf.canvas.focus_set = lambda: taken.append("page")    # (a hidden window can't take the focus)
        sf.canvas.winfo_viewable = lambda: True
        self.radio(3).event_generate("<ButtonPress-1>")       # through the window-wide binding
        self.radio(3).event_generate("<ButtonRelease-1>")
        self.assertEqual(taken, ["page"])

    def test_a_link_the_pointer_focuses_stays_put_but_is_marked(self):
        from projectionist.ui import theme as T  # noqa: F401
        import tkinter as tk
        from tkinter import font as tkfont
        holder = tk.Frame(self.root)
        holder.place(x=0, y=0, width=400, height=300)
        sf = self.W.ScrollFrame(holder)
        sf.pack(fill="both", expand=True)
        links = [self.W.LinkLabel(sf.inner, f"Link {i}", lambda: None) for i in range(40)]
        for link in links:
            link.pack(anchor="w")
        self.root.update()
        links[35].event_generate("<ButtonPress-1>")
        links[35].event_generate("<FocusIn>")
        self.assertEqual(sf.canvas.yview()[0], 0.0)           # the page didn't move under the pointer...
        font = links[35].cget("font")
        self.assertEqual(int(tkfont.Font(root=self.root, font=font).actual("underline")), 1)   # ...it's marked
        links[35].event_generate("<ButtonRelease-1>")
        links[35].event_generate("<FocusIn>")                 # Tab to it: shown
        self.assertGreater(sf.canvas.yview()[0], 0.5)


if __name__ == "__main__":
    unittest.main()
