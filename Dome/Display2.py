from __future__ import generators

import rox
from rox import g
from xml.dom import Node
import pango
from constants import XMLNS_NAMESPACE
import gobject

import __main__
default_font = __main__.default_font

drag_cursor = g.gdk.Cursor(g.gdk.HAND1)

def calc_node(display, node, pos):
	attribs = []
	if node.nodeType == Node.TEXT_NODE:
		text = node.nodeValue.strip()
	elif node.nodeType == Node.ELEMENT_NODE:
		if node.namespaceURI:
			text = display.view.model.namespaces.prefix.get(node.namespaceURI, 'ERROR') + \
				':' + node.localName
		else:
			text = node.localName
	elif node.nodeType == Node.ATTRIBUTE_NODE:
		if node.namespaceURI:
			text = display.view.model.namespaces.prefix.get(node.namespaceURI, 'ERROR') + \
				':' + node.localName
		else:
			text = node.localName
		text = ' %s=%s' % (unicode(text), unicode(node.value))
	elif node.nodeType == Node.COMMENT_NODE:
		text = node.nodeValue.strip()
	elif node.nodeType == Node.DOCUMENT_NODE:
		chroots = len(display.view.chroots)
		if chroots > 1:
			text = "subtree (%d levels)" % chroots
		elif chroots == 1:
			text = 'subtree (one level)'
		else:
			text = display.view.model.uri
	elif node.nodeName:
		text = node.nodeName
	elif node.nodeValue:
		text = '<noname>' + node.nodeValue
	else:
		text = '<unknown>'

	# PyGtk leaks PangoLayouts, so just reuse a single one
	layout = display.surface_layout
	layout.set_text(text)
	width, height = layout.get_pixel_size()
	x, y = map(int, pos)

	text_x = x
	if node.nodeType != Node.ATTRIBUTE_NODE:
		text_x += 12

	def draw_fn():
		surface = display.pm
		style = display.surface.style	# Different surface ;-)
		fg = style.fg_gc
		bg = style.bg_gc

		if node in display.selection:
			state = g.STATE_SELECTED
		else:
			state = g.STATE_NORMAL

		if node.nodeType != Node.ATTRIBUTE_NODE:
			if node.nodeType == Node.ELEMENT_NODE:
				surface.draw_rectangle(style.fg_gc[state], False, x, y, 7, height - 2)
				surface.draw_rectangle(style.bg_gc[state], True, x + 1, y + 1, 6, height - 3)
			elif node.nodeType == Node.DOCUMENT_NODE:
				surface.draw_arc(style.fg_gc[state], False, x, y, 7, height - 2, 0, 64 * 360)
			else:
				# Text, etc
				surface.draw_rectangle(style.text_gc[state], False, x, y, 7, height - 2)
				surface.draw_rectangle(style.base_gc[state], True, x + 1, y + 1, 6, height - 3)
			
			if node in display.view.model.hidden:
				surface.draw_layout(fg[g.STATE_PRELIGHT], text_x + width + 2, y,
					display.create_pango_layout('(%s)' % display.view.model.hidden[node]))
		
		if node in display.selection:
			surface.draw_rectangle(bg[g.STATE_SELECTED], True,
				text_x, y, width - 1, height - 1)
			surface.draw_layout(fg[g.STATE_SELECTED], text_x, y, layout)
		else:
			if node.nodeType == Node.TEXT_NODE:
				gc = style.text_gc[g.STATE_NORMAL]
			elif node.nodeType == Node.ATTRIBUTE_NODE:
				gc = style.fg_gc[g.STATE_INSENSITIVE]
			elif node.nodeType == Node.COMMENT_NODE:
				gc = style.text_gc[g.STATE_INSENSITIVE]
			else:
				gc = style.fg_gc[g.STATE_NORMAL]
			surface.draw_layout(gc, text_x, y, layout)

		if node in display.view.marked:
			surface.draw_rectangle(style.text_gc[g.STATE_PRELIGHT], False,
					x - 1, y - 1, width + (text_x - x), height)

	bbox = (x, y, text_x + width, y + height)
	return bbox, draw_fn

class Display(g.HBox):
	visible = 1		# Always visible

	def __init__(self, window, view):
		g.HBox.__init__(self, False, 0)

		self.surface = g.EventBox()
		self.surface_layout = self.surface.create_pango_layout('')
		self.pack_start(self.surface, True, True, 0)
		self.surface.show()
		self.surface.set_app_paintable(True)
		self.surface.set_double_buffered(False)
		self.update_timeout = 0
		self.cached_nodes = None

		self.scroll_adj = g.Adjustment(lower = 0, upper = 100, step_incr = 1)
		self.scroll_adj.connect('value-changed', self.scroll_to)
		scale = g.VScrollbar(self.scroll_adj)
		scale.unset_flags(g.CAN_FOCUS)
		#scale.set_draw_value(False)
		self.pack_start(scale, False, True, 0)
		
		self.view = None
		self.parent_window = window
		self.pm = None

		s = self.surface.get_style().copy()
		s.bg[g.STATE_NORMAL] = g.gdk.color_parse('old lace')
		s.text[g.STATE_NORMAL] = g.gdk.color_parse('blue')
		s.text[g.STATE_PRELIGHT] = g.gdk.color_parse('orange')	# Mark
		s.text[g.STATE_INSENSITIVE] = g.gdk.color_parse('dark green')# Comment
		s.fg[g.STATE_PRELIGHT] = g.gdk.color_parse('red')	# Hidden
		self.surface.set_style(s)

		self.signals = [self.connect('destroy', self.destroyed)]
		self.surface.connect('button-press-event', self.bg_event)
		self.surface.connect('motion-notify-event', self.bg_motion)
		self.surface.connect('button-release-event', self.bg_event)

		# Display is relative to this node, which is the highest
		# displayed node (possibly off the top of the screen)
		self.ref_node = view.root
		self.ref_pos = (0, 0)

		self.drag_info = None

		self.last_alloc = None
		self.surface.connect('size-allocate', lambda w, a: self.size_allocate(a))
		self.surface.connect('size-request', lambda w, r: self.size_request(r))
		def expose(w, e):
			area = e.area
			w.window.clear_area(area.x, area.y, area.width, area.height)
			return True
		self.surface.connect('expose-event', expose)

		self.pan_timeout = None
		self.h_limits = (0, 0)
		self.set_view(view)
	
	def destroyed(self, widget):
		self.view.remove_display(self)
		for s in self.signals:
			self.disconnect(s)
		if self.update_timeout:
			g.timeout_remove(self.update_timeout)
			self.update_timeout = 0

		#del self.selection
		del self.view
		del self.parent_window
		del self.ref_node
		del self.surface_layout
		del self.surface
		del self.scroll_adj
		del self.drawn
		del self.pm
	
	def size_allocate(self, alloc):
		new = (alloc.width, alloc.height)
		if self.last_alloc == new:
			return
		self.last_alloc = new
		assert self.window
		#print "Alloc", alloc.width, alloc.height
		pm = g.gdk.Pixmap(self.surface.window, alloc.width, alloc.height, -1)
		self.surface.window.set_back_pixmap(pm, False)
		self.pm = pm

		if self.update_timeout:
			g.timeout_remove(self.update_timeout)
			self.update_timeout = 0
		self.update()

	def update(self):
		# Must be called either:
		# - With no update_timeout running, or
		# - From the timeout callback

		self.update_timeout = 0

		if not self.pm: return 0
		#print "update"

		self.pm.draw_rectangle(self.surface.style.bg_gc[g.STATE_NORMAL], True,
				  0, 0, self.last_alloc[0], self.last_alloc[1])

		self.drawn = {}	# xmlNode -> ((x1, y1, y2, y2), attrib_parent)

		n = self.ref_node
		p = self.view.root.parentNode
		while n is not p:
			n = n.parentNode
			if not n:
				print "(lost root)"
				self.ref_node = self.view.root
				self.ref_pos = (0, 0)
				break

		if self.view.current_attrib:
			self.selection = {self.view.current_attrib: None}
		else:
			self.selection = {}
			for n in self.view.current_nodes:
				self.selection[n] = None

		pos = list(self.ref_pos)
		self.h_limits = (self.ref_pos[0], self.ref_pos[0])	# Left, Right
		node = self.ref_node
		attr_parent = None
		drawn = 0
		for node, bbox, draw_fn in self.walk_tree(self.ref_node, self.ref_pos):
			if bbox[1] > self.last_alloc[1]: break	# Off-screen
			if bbox[1] > -self.last_alloc[1]:
				draw_fn()
				drawn += 1
			else:
				pass#print 'Warning: Ref node way off:', bbox[1]
			if node.nodeType == Node.ATTRIBUTE_NODE:
				self.drawn[node] = (bbox, attr_parent)
			else:
				attr_parent = node
				self.drawn[node] = (bbox, None)

			if bbox[1] < 0 and node.nodeType != Node.ATTRIBUTE_NODE:
				self.ref_node = node
				self.ref_pos = bbox[:2]
			self.h_limits = (min(self.h_limits[0], bbox[0]),
					 max(self.h_limits[1], bbox[2]))
		else:
			# Didn't have enough nodes to fill the screen
			frac_filled = float(bbox[3]) / self.last_alloc[1]
			if frac_filled:
				drawn /= frac_filled

		self.surface.window.clear()

		# Update adjustment
		self.ensure_cache()
		try:
			pos = self.cached_nodes.index(self.ref_node)
		except:
			pos = 0
			print "Missing ref node!!"
		self.scroll_adj.value = float(pos)
		self.scroll_adj.upper = float(len(self.cached_nodes) + drawn)

		self.scroll_adj.page_size = float(drawn)
		self.scroll_adj.page_increment = float(drawn)

		return 0
	
	def ensure_cache(self):
		"Find all the nodes in the document, in document order. Not attributes."
		if self.cached_nodes is not None:
			return
		nodes = [self.view.root.parentNode]
		node = self.view.root
		hidden = self.view.model.hidden
		while node:
			nodes.append(node)
			if node.childNodes and node not in hidden:
				node = node.childNodes[0]
			else:
				while not node.nextSibling:
					node = node.parentNode
					if not node:
						self.cached_nodes = nodes
						return
				node = node.nextSibling
		self.cached_nodes = nodes
	
	def walk_tree(self, node, pos):
		"""Yield this (node, bbox), and all following ones in document order."""
		pos = list(pos)
		hidden = self.view.model.hidden
		while node:
			bbox, draw_fn = calc_node(self, node, pos)
			yield (node, bbox, draw_fn)

			if node.nodeType == Node.ELEMENT_NODE:
				if node not in hidden:
					apos = [bbox[2] + 4, bbox[1]]
					for key in node.attributes:
						a = node.attributes[key]
						if a.namespaceURI == XMLNS_NAMESPACE:
							continue
						abbox, draw_fn = calc_node(self, a, apos)
						apos[0] = abbox[2] + 4
						yield (a, abbox, draw_fn)
			
			pos[1] = bbox[3] + 2
			if node.childNodes and node not in hidden:
				node = node.childNodes[0]
				pos[0] += 16
			else:
				while not node.nextSibling:
					node = node.parentNode
					if not node: return
					pos[0] -= 16
				node = node.nextSibling
	
	def size_request(self, req):
		req.width = 4
		req.height = 4

	def do_update_now(self):
		# Update now, if we need to
		if self.update_timeout:
			gobject.source_remove(self.update_timeout)
			self.update_timeout = 0
			self.update()

	def update_all(self, node = None):
		self.cached_nodes = None

		if self.update_timeout:
			return		# Going to update anyway...

		if self.view.running():
			self.update_timeout = gobject.timeout_add(2000, self.update)
		else:
			self.update_timeout = gobject.timeout_add(10, self.update)
	
	def move_from(self, old = []):
		if not self.pm: return
		if self.view.current_nodes:
			selection = {}
			for n in self.view.current_nodes:
				selection[n] = None
			shown = False
			for node, bbox, draw_fn in self.walk_tree(self.ref_node, self.ref_pos):
				if bbox[1] > self.last_alloc[1]: break	# Off-screen
				if bbox[3] > 0 and node in selection:
					shown = True
					break	# A selected node is shown
			if not shown:
				#print "(selected nodes not shown)"
				self.ref_node = node = self.view.current_nodes[0]
				self.ref_pos = (40, self.last_alloc[1] / 2)

				all_hidden = self.view.model.hidden
				while node:
					node = node.parentNode
					if node in all_hidden:
						self.ref_node = node

				self.backup_ref_node()
		self.update_all()

	def set_view(self, view):
		if self.view:
			self.view.remove_display(self)
		self.view = view
		self.view.add_display(self)
		self.update_all()

	def show_menu(self, bev):
		pass
	
	def node_clicked(self, node, event):
		pass

	def xy_to_node(self, x, y):
		"Return the node at this point and, if it's an attribute, its parent."
		for (n, ((x1, y1, x2, y2), attrib_parent)) in self.drawn.iteritems():
			if x >= x1 and x <= x2 and y >= y1 and y <= y2:
				return n, attrib_parent
		return None, None
	
	def pan(self):
		def scale(x):
			val = (float(abs(x)) ** 1.4)
			if x < 0:
				return -val
			else:
				return val
		def chop(x):
			if x > 10: return x - 10
			if x < -10: return x + 10
			return 0
		x, y, mask = self.surface.window.get_pointer()
		sx, sy = self.pan_start
		dx, dy = scale(chop(x - sx)) / 20, scale(chop(y - sy))
		dx = max(dx, 10 - self.h_limits[1])
		dx = min(dx, self.last_alloc[0] - 10 - self.h_limits[0])
		new = [self.ref_pos[0] + dx, self.ref_pos[1] + dy]
		
		if new == self.ref_pos:
			return 1

		self.ref_pos = new

		self.backup_ref_node()

		if self.update_timeout:
			g.timeout_remove(self.update_timeout)
			self.update_timeout = 0
		self.update()
		
		return 1
	
	def backup_ref_node(self):
		self.ref_pos = list(self.ref_pos)
		# Walk up the parents until we get a ref node above the start of the screen
		# (redraw will come back down)
		while self.ref_pos[1] > 0:
			src = self.ref_node

			if self.ref_node.previousSibling:
				self.ref_node = self.ref_node.previousSibling
			elif self.ref_node.parentNode:
				self.ref_node = self.ref_node.parentNode
			else:
				break

			# Walk from the parent node to find how far it is to this node...
			for node, bbox, draw_fn in self.walk_tree(self.ref_node, (0, 0)):
				if node is src: break
			else:
				assert 0

			self.ref_pos[0] -= bbox[0]
			self.ref_pos[1] -= bbox[1]

			#print "(start from %s at (%d,%d))" % (self.ref_node, self.ref_pos[0], self.ref_pos[1])

		if self.ref_pos[1] > 10:
			self.ref_pos[1] = 10
		elif self.ref_pos[1] < -100:
			for node, bbox, draw_fn in self.walk_tree(self.ref_node, self.ref_pos):
				if bbox[3] > 10: break	# Something is visible
			else:
				self.ref_pos[1] = -100

	def bg_motion(self, widget, event):
		if not self.drag_info:
			return
		node, attr_parent, x, y, in_progress = self.drag_info
		if not in_progress:
			if abs(event.x - x) > 5 or abs(event.y - y) > 5:
				self.drag_info = (node, attr_parent, event.x, event.y, True)
				self.window.set_cursor(drag_cursor)
			else:
				return False
		return False
	
	def do_drag(self, src, dst):
		pass

	def bg_event(self, widget, event):
		if event.type == g.gdk.BUTTON_PRESS and event.button == 3:
			self.show_menu(event)
		elif event.type == g.gdk.BUTTON_PRESS or event.type == g.gdk._2BUTTON_PRESS:
			self.do_update_now()
			node, attr_parent = self.xy_to_node(event.x, event.y)
			if event.button == 1:
				self.drag_info = (node, attr_parent, event.x, event.y, False)
				if node:
					if attr_parent:
						self.attrib_clicked(attr_parent, node, event)
					else:
						self.node_clicked(node, event)
			elif event.button == 2:
				assert self.pan_timeout is None
				self.pan_start = (event.x, event.y)
				self.pan_timeout = gobject.timeout_add(100, self.pan)
		elif event.type == g.gdk.BUTTON_RELEASE:
			if event.button == 2:
				assert self.pan_timeout is not None
				gobject.source_remove(self.pan_timeout)
				self.pan_timeout = None
			elif event.button == 1 and self.drag_info:
				src_node, src_attr_parent, x, y, in_progress = self.drag_info
				if not in_progress:
					return True
				dst_node, dst_attr_parent = self.xy_to_node(event.x, event.y)
				self.window.set_cursor(None)
				if not dst_node or (dst_node == src_node and dst_attr_parent == src_attr_parent):
					return True
				self.do_drag((src_node, src_attr_parent), (dst_node, dst_attr_parent))
		else:
			return False
		return True

	def marked_changed(self, nodes):
		"nodes is a list of nodes to be rechecked."
		self.update_all()

	def options_changed(self):
		if default_font.has_changed:
			#self.modify_font(pango.FontDescription(default_font.value))
			self.update_all()
	
	def scroll_to(self, adj):
		n = int(adj.value)
		self.ensure_cache()
		try:
			node = self.cached_nodes[n]
		except:
			node = self.cached_nodes[-1]
		if self.ref_node == node:
			return
		self.ref_node = node
		x = 0
		while node.parentNode:
			x += 16
			node = node.parentNode
		self.ref_pos = (x, 0)
		self.update_all()
	
	def set_status(self, message):
		self.parent_window.set_status(message)
