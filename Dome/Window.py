from gtk import *
from GDK import *
from _gtk import *
import string

from xml.dom import Node
from xml.dom import ext
from xml.dom import implementation
from xml.dom.ext.reader import PyExpat

import Html
from loader import make_xds_loader
from support import *
from SaveBox import SaveBox
from List import List
from Toolbar import Toolbar

from Model import Model
from View import View
from GUIView import GUIView

import Exec

def strip_space(doc):
	def cb(node, cb):
		if node.nodeType == Node.TEXT_NODE:
			node.data = string.strip(node.data)
			if node.data == '':
				node.parentNode.removeChild(node)
		else:
			for k in node.childNodes[:]:
				cb(k, cb)
	cb(doc.documentElement, cb)

class Window(GtkWindow):
	def __init__(self, path = None):
		GtkWindow.__init__(self)
		
		self.model = Model(List(self))
		self.gui_view = None
		
		self.set_default_size(gdk_screen_width() * 2 / 3,
				      gdk_screen_height() * 2 / 3)
		self.set_position(WIN_POS_CENTER)
		self.savebox = None

		vbox = GtkVBox(FALSE, 0)
		self.add(vbox)
		tb = Toolbar(self)
		vbox.pack_start(tb, FALSE, TRUE, 0)

		hbox = GtkHBox(FALSE, 0)
		vbox.pack_start(hbox)

		self.model.macro_list.load_all()
		hbox.pack_start(self.model.macro_list, FALSE, TRUE, 0)
		
		self.uri = "Document"

		if path:
			if path != '-':
				self.uri = path
			self.load_file(path)

		swin = GtkScrolledWindow()
		hbox.pack_start(swin, TRUE, TRUE, 0)

		view = View(self.model)
		Exec.exec_state = Exec.Exec(view)
		self.gui_view = GUIView(self, view)
		swin.add(self.gui_view)
		swin.set_hadjustment(self.gui_view.get_hadjustment())
		swin.set_vadjustment(self.gui_view.get_vadjustment())
		swin.set_policy(POLICY_AUTOMATIC, POLICY_ALWAYS)

		vbox.show_all()
		self.connect('key-press-event', self.key)
		make_xds_loader(self, self)
	
		self.gui_view.grab_focus()
		self.update_title()
		self.connect('destroy', self.destroyed)
	
	def destroyed(self, widget):
		print "Saving macros..."
		self.model.macro_list.save_all()

	def load_file(self, path):
		if path[-5:] == '.html':
			self.model.load_html(path)
			self.uri = path[:-5] + '.xml'
		else:
			self.model.load_xml(path)
			self.uri = path
		self.update_title()

	def load_data(self, data):
		report_error("Can only load files for now - sorry")
	
	def update_title(self):
		title = self.uri
		if self.gui_view and self.gui_view.view.recording_where:
			title += ' (recording)'
		self.set_title(title)
	
	def key(self, widget, kev):
		if kev.keyval == F3:
			if kev.state & SHIFT_MASK:
				self.model.macro_list.save_all()
			else:
				self.save()
		return 1
	
	def save(self):
		if self.savebox:
			self.savebox.destroy()
		self.savebox = SaveBox(self, 'text', 'xml')
		self.savebox.show()
	
	def get_xml(self):
		self.output_data = ''
		ext.PrettyPrint(node_to_xml(self.gui_view.view.root), stream = self)
		d = self.output_data
		self.output_data = ''
		return d
	
	def write(self, text):
		self.output_data = self.output_data + text

	def save_as(self, path):
		return send_to_file(self.get_xml(), path)

	def send_raw(self, selection_data):
		selection_data.set(selection_data.target, 8, self.get_xml())
		
	def set_uri(self, uri):
		self.uri = uri
		self.update_title()

	# Toolbar bits

	tools = [
		('Save', 'Save this macro'),
		('Record', 'Start recording a new macro'),
		('Extend', 'Record extra steps at the current point'),
		('Stop', 'Stop a running macro'),
		('Play', 'Run this macro from the start'),
		('Next', 'Run until the next step in this macro'),
		('Step', 'Run one step, stopping in any macro'),
		]
	
	def tool_Save(self):
		self.save()
	
	def tool_Stop(self):
		Exec.exec_state.stop()

	def tool_Play(self):
		Exec.exec_state.set_step_mode(-1)
		Exec.exec_state.sched()
	
	def tool_Next(self):
		Exec.exec_state.set_step_mode(1)
		Exec.exec_state.do_one_step()
	
	def tool_Step(self):
		Exec.exec_state.set_step_mode(0)
		Exec.exec_state.do_one_step()
	
	def tool_Record(self):
		self.gui_view.view.toggle_record()
	
	def tool_Extend(self):
		self.gui_view.view.toggle_record(extend = TRUE)
