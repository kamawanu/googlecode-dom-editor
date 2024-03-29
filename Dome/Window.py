import rox
from rox import g, TRUE, FALSE, saving

import os.path
from Ft.Xml.Domlette import PrettyPrint

import __main__

#from support import *
from rox.saving import SaveBox

code = None

from codecs import lookup
utf8_encoder = lookup('UTF-8')[0]

class Window(rox.Window, saving.Saveable):
	def __init__(self, path = None, data = None):
		# 'data' is used when 'path' is a stylesheet...
		rox.Window.__init__(self)

		self.status = g.Statusbar()
		self.showing_message = False
		self.set_status('Welcome to Dome. Click the right-hand mouse button for menus.')
		
		# Make it square, to cope with Xinerama
		size = min(g.gdk.screen_width(), g.gdk.screen_height())
		size = size * 3 / 4
		
		self.set_default_size(size, size)
		self.set_position(g.WIN_POS_CENTER)
		self.savebox = None

		self.show()
		g.gdk.flush()

		if path:
			import os.path
			path = os.path.abspath(path)
			
		import Model
		self.model = Model.Model(path, dome_data = data)
		self.gui_view = None
		self.dome_state = ""
		
		from GUIView import GUIView
		#from Display2 import Display as GUIView
		from List import List

		vbox = g.VBox(FALSE, 0)
		self.add(vbox)

		tools = g.Toolbar()
		tools.set_style(g.TOOLBAR_ICONS)
		vbox.pack_start(tools, FALSE, TRUE, 0)
		tools.show()

		tips = g.Tooltips()

		for stock, label, callback in [
				(g.STOCK_GO_UP, 'Up', self.tool_parent),
				(g.STOCK_SAVE, 'Save', self.tool_save),
				(g.STOCK_MEDIA_RECORD, 'Record', self.tool_record),
				(g.STOCK_MEDIA_STOP, 'Stop', self.tool_stop),
				(g.STOCK_MEDIA_PLAY, 'Play', self.tool_play),
				(g.STOCK_MEDIA_NEXT, 'Next', self.tool_next),
				(g.STOCK_MEDIA_NEXT, 'Step', self.tool_step),
				(g.STOCK_HELP, 'Help', self.tool_help) ]:
			item = g.ToolButton(stock)
			item.set_label(label)
			item.set_tooltip(tips, label)
			item.connect('clicked', callback)
			tools.add(item)

		paned = g.HPaned()
		vbox.pack_start(paned)

		import View
		view = View.View(self.model)
		self.view = view
		self.list = List(view)

		paned.add1(self.list)
		self.list.show()

		#swin = g.ScrolledWindow()
		#swin.set_policy(g.POLICY_AUTOMATIC, g.POLICY_ALWAYS)
		#paned.add2(swin)
		paned.set_position(200)

		self.gui_view = GUIView(self, view)
		#swin.add(self.gui_view)
		paned.add2(self.gui_view)
		#swin.set_hadjustment(self.gui_view.get_hadjustment())
		#swin.set_vadjustment(self.gui_view.get_vadjustment())

		vbox.pack_start(self.status, False, True, 0)

		vbox.show_all()
	
		self.gui_view.grab_focus()
		self.update_title()

		def delete(window, event):
			if self.model.root_program.modified:
				self.save(discard = 1)
				return 1
			return 0
		self.connect('delete-event', delete)
	
	def set_status(self, message = None):
		if self.showing_message:
			self.status.pop(0)
		if message:
			self.status.push(0, message)
		self.showing_message = bool(message)
		#self.status.draw((0, 0, self.status.allocation.width, self.status.allocation.height))
		g.gdk.flush()
	
	def discard(self):
		self.destroy()
		
	def set_state(self, state):
		if state == self.dome_state:
			return
		if state:
			self.dome_state = " " + state
		else:
			self.dome_state = ""

		self.update_title()

	def update_title(self):
		title = self.model.uri
		self.set_title(title + self.dome_state)
	
	def save(self, discard = 0):
		if self.savebox:
			self.savebox.destroy()
		path = self.model.uri

		self.savebox = saving.SaveBox(self, path,
					'application/x-dome', discard = discard)
		self.savebox.set_transient_for(self)
		self.savebox.set_destroy_with_parent(True)
		radio_dome = g.RadioButton(None, 'Save everything (.dome)')
		radio_xml = g.RadioButton(radio_dome, 'Export data as XML')
		radio_html = g.RadioButton(radio_xml, 'Export data as XHTML')

		self.save_radios = (
			(radio_dome, 'dome', 'application/x-dome', self.save_as_dome),
			(radio_xml, 'xml', 'text/xml', self.save_as_xml),
			(radio_html, 'xhtml', 'application/xhtml+xml', self.save_as_html))
				    
		def changed(toggle = None):
			name = self.savebox.save_area.entry.get_text()
			if name.endswith('.xml'):
				name = name[:-4]
			elif name.endswith('.dome'):
				name = name[:-5]
			elif name.endswith('.xhtml'):
				name = name[:-6]

			for radio, ext, mime, fn in self.save_radios:
				if radio.get_active():
					name += '.' + ext
					self.savebox.set_type(mime)
			self.savebox.save_area.entry.set_text(name)

		for radio, ext, mime, fn in self.save_radios:
			if path.endswith('.' + ext):
				radio.set_active(True)
				self.savebox.set_type(mime)
				break
		else:
			self.save_radios[1][0].set_active(True)
			self.savebox.set_type(self.save_radios[1][2])

		for radio, ext, mime, fn in self.save_radios:
			self.savebox.vbox.pack_start(radio, False, True, 0)
			radio.connect('toggled', changed)

		self.savebox.vbox.show_all()
		self.savebox.show()
	
	def write(self, text):
		if type(text) == unicode:
			text = utf8_encoder(text)[0]
		self.output_data.write(text)

	def save_to_stream(self, stream):
		self.set_status('Saving...')
		try:
			for radio, ext, mime, fn in self.save_radios:
				if radio.get_active():
					fn(stream)
		finally:
			self.set_status()

	def save_as_dome(self, stream):
		#self.view.model.strip_space()
		doc = self.view.export_all()
		PrettyPrint(doc, stream)

	def save_as_xml(self, stream):
		doc = self.view.model.doc
		PrettyPrint(doc, stream)
		
	def save_as_html(self, stream):
		from xml.dom.html import HTMLDocument
		import to_html
		print >>stream, to_html.to_html(self.view.root.ownerDocument)

	def set_uri(self, uri):
		if self.save_radios[0][0].get_active():
			self.model.uri = uri
			self.model.root_program.modified = 0
			self.update_title()

	# Toolbar bits

	def tool_parent(self, button = None):
                if '/' in self.model.uri:
                        rox.filer.show_file(self.model.uri)
                else:
                        rox.alert("File is not saved to disk yet")
		
	def tool_save(self, button = None):
		self.save()
	
	def tool_stop(self, button = None):
		if self.view.rec_point:
			self.view.stop_recording()
		if self.view.running():
			self.view.single_step = 1
		else:
			self.view.run_new()

	def tool_play(self, button = None):
		from View import InProgress, Done
		if not self.view.exec_point:
			if self.view.rec_point:
				self.view.set_exec(self.view.rec_point)
				self.view.set_rec(None)
			else:
				rox.alert('No playback point!')
				return
		# Step first, in case we're on a breakpoint
		self.view.single_step = 1
		try:
			self.view.do_one_step()
		except InProgress:
			self.view.single_step = 0
			return
		except Done:
			pass
		self.view.single_step = 0
		self.view.sched()
	
	def tool_next(self, button = None):
		from View import InProgress, Done
		self.view.single_step = 2
		try:
			self.view.do_one_step()
		except InProgress, Done:
			pass
	
	def tool_step(self, button = None):
		from View import InProgress, Done
		self.view.single_step = 1
		try:
			self.view.do_one_step()
		except InProgress, Done:
			pass
	
	def tool_record(self, button = None):
		if self.view.rec_point:
			self.view.stop_recording()
		else:
			if not self.view.rec_point:
				prog = self.list.selected_program()
				if prog:
					start = prog.code.start
					if start.next == None:
						self.view.set_rec((start, 'next'))
						return
			self.view.record_at_point()
	
	def tool_help(self, button = None):
		rox.filer.show_file(os.path.join(rox.app_dir, 'Help', 'README'))
