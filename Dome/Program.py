from xml.parsers.xmlproc.utils import escape_attval
from xml.dom.ext.reader import PyExpat
from StringIO import StringIO
import string

from support import *

def el_named(node, name):
	for n in node.childNodes:
		if n.localName == name:
			return n
	return None
	
def load_xml(op_xml):
	"op_xml is '<chain>...</chain>'"
	reader = PyExpat.Reader()
	doc = reader.fromStream(StringIO(op_xml))
	return load(doc.documentElement)

# Node is a DOM <dome-program> or <node> node.
# Returns the start Op.
def load(program, chain):
	start = None
	prev = None
	for op_node in chain.childNodes:
		if str(op_node.localName) != 'node':
			continue
		
		attr = op_node.getAttributeNS('', 'action')
		action = eval(str(attr))
		if action[0] == 'chroot':
			action[0] = 'enter'
		elif action[0] == 'unchroot':
			action[0] = 'leave'
		elif action[0] == 'playback':
			action[0] = 'map'

		op = Op(program, action)

		if not start:
			start = op
		if prev:
			prev.link_to(op, 'next')
		prev = op
		
		fail = load(program, op_node)
		if fail:
			op.link_to(fail, 'fail')
	return start

def load_dome_program(prog):
	"prog should be a DOM 'dome-program' node."
	if prog.nodeName != 'dome-program':
		raise Exception('Not a DOME program!')

	new = Program(str(prog.getAttributeNS('', 'name')))

	start = load(new, prog)
	if start:
		new.set_start(start)

	print "Loading '%s'..." % new.name

	for node in prog.childNodes:
		if node.localName == 'dome-program':
			new.add_sub(load_dome_program(node))
		
	return new

class Program:
	"A program contains a Start Op and any number of sub-programs."
	def __init__(self, name, start = None):
		if not start:
			start = Op(self)
		self.start = start
		self.name = name
		self.subprograms = []
		self.watchers = []
		self.parent = None
	
	def set_start(self, start):
		self.start = start
		self.changed(None)

	def changed(self, op):
		#if op:
			#print "%s: Op(%s) changed." % (self.name, op.action)
		#else:	
			#print "%s: Changed" % self.name
		for w in self.watchers:
			w.program_changed(self)
	
	def add_sub(self, prog):
		if prog.parent:
			raise Exception('%s already has a parent program!' % prog)
		prog.parent = self
		self.subprograms.append(prog)
		self.changed(None)
		prog.changed(None)
	
	def remove_sub(self, prog):
		if prog.parent != self:
			raise Exception('%s is no child of mime!' % prog)
		prog.parent = None
		self.subprograms.remove(prog)
		self.changed(None)
		prog.changed(None)
	
	def rename(self, name):
		self.name = name
		self.changed(None)
		if self.parent:
			self.parent.changed(None)
		for k in self.subprograms:
			k.changed(None)
	
	def to_xml(self):
		data = "<dome-program name='%s'>\n" % escape_attval(self.name)
	
		data += self.start.to_xml_int()

		for p in self.subprograms:
			data += p.to_xml()

		return data + "</dome-program>\n"
	
class Op:
	"Each node in a chain is an Op. There is no graphical stuff in here."

	def __init__(self, program, action = None):
		"Creates a new node (can be linked into another node later)"
		if not action:
			action = ['Start']
		if not isinstance(program, Program):
			raise Exception('Not a program!')
		self.program = program
		self.action = action
		self.next = None
		self.fail = None
		self.prev = None
	
	def changed(self):
		self.program.changed(self)
	
	def link_to(self, child, exit):
		# Create a link from this exit to this child Op
		if getattr(self, exit) != None:
			raise Exception('%s already has a %s child op!' % (self, exit))
		setattr(self, exit, child)
		self.changed()
	
	def unlink(self, child):
		"child becomes a Start node"
		if child.prev != self:
			raise Exception('forget_child: not my child!')
		child.prev = None

		if child == self.next:
			exit = 'next'
		else:
			exit = 'fail'
		setattr(self, exit, None)

		self.changed()

	def swap_nf(self):
		self.next, self.fail = (self.fail, self.next)
		self.changed()
	
	def del_node(self):
		"Remove this node, linking our next and previous nodes."
		"Error if we have fail and next nodes..., or if we have no prev node"
		if self.next and self.fail:
			raise Exception("Can't delete a node with both fail and next exits in use.")
		if not self.prev:
			raise Exception("Can't delete a Start node!")

		if self.next:
			next = self.next
		else:
			next = self.fail

		if next:
			next.prev = None

		prev = self.prev
		if prev.next == self:
			exit = 'next'
		else:
			exit = 'fail'
		prev.link_to(next, exit)

		self.action = None
		self.prev = None
		self.next = None
		self.fail = None
		self.program = None
	
	def del_chain(self):
		xml = self.to_xml()
		self.prev.unlink(self)
		return xml
	
	def to_xml(self):
		return '<dome-program>\n' + self.to_xml_int() + '</dome-program>\n'
		
	def to_xml_int(self):
		"Returns a chain of <Node> elements. So, if you want XML, enclose it "
		"in something."
		next = self.next
		fail = self.fail
		act = escape_attval(`self.action`)
		ret = '<node action="%s"' % act
		if fail == None:
			ret += '/>\n'
		else:
			ret += '>\n' + self.fail.to_xml_int() + '</node>'

		if self.next:
			ret += self.next.to_xml_int()
		return ret
	
	def __str__(self):
		return "{" + `self.action` + "}"
