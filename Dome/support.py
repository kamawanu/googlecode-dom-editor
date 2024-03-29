import rox

import os, sys
import traceback
from xml.dom import Node
from Ft.Xml.Domlette import implementation
from Ft.Xml import XMLNS_NAMESPACE

from string import find, lower, join
from socket import gethostbyaddr, gethostname

import re
entrefpattern = re.compile('&(\D\S+);')

def node_to_xml(node):
	"Takes an XML node and returns an XML documentElement suitable for saving."
	root = implementation.createDocument(None, 'root', None)
	new = node.cloneNode(1)
	new = root.importNode(new, 1)
	root.replaceChild(new, root.documentElement)
	return root

def node_to_html(node):
	"Takes an XML node and returns an HTML documentElement suitable for saving."
	root = implementation.createHTMLDocument('HTML document')
	def html(doc, node, html):
		new = doc.importNode(node.cloneNode(deep = 0), deep = 0)
		if node.nodeType == Node.ELEMENT_NODE:
			for a in node.attributes:
				new.setAttribute(a.localName, a.value)
			for k in node.childNodes:
				new.appendChild(html(doc, k, html))
		return new
	new = html(root, node, html)
	root.replaceChild(new, root.documentElement)
	return root

def send_to_file(data, path):
	try:
		file = open(path, 'wb')
		try:
			file.write(data)
		finally:
			file.close()
	except:
		rox.report_exception()
		return 0

	return 1

def fix_broken_html(data):
	"""Pre-parse the data before sending to tidy to fix really really broken
stuff (eg, MS Word output). Returns None if data is OK"""
	if data.find('<o:p>') == -1:
		return 		# Doesn't need fixing?
	import re
	data = data.replace('<o:p></o:p>', '')
	data = re.sub('<!\[[^]]*\]>', '', data)
	return data

def to_html_doc(data):
	"Run data though tidy and return the resulting XML text"
	(r, w) = os.pipe()
	child = os.fork()
	#data = data.replace('&nbsp;', ' ')
	#data = data.replace('&copy;', '(c)')
	#data = data.replace('&auml;', '(auml)')
	#data = data.replace('&ouml;', '(ouml)')
	fixed = fix_broken_html(data)
	if child == 0:
		# We are the child
		try:
			os.close(r)
			os.dup2(w, 1)
			os.close(w)
			if fixed:
				tin = os.popen('tidy --force-output yes -q -utf8 -asxml 2>/dev/null', 'w')
			else:
				tin = os.popen('tidy --force-output yes -q -asxml 2>/dev/null', 'w')
			tin.write(fixed or data)
			tin.close()
		finally:
			os._exit(0)
	os.close(w)
	
	data = os.fdopen(r).read()
	os.waitpid(child, 0)

	return data

def parse_data(data, path):
	"""Convert and XML document into a DOM Document."""
	from Ft.Xml.InputSource import InputSourceFactory
	#from Ft.Xml.cDomlette import NonvalParse
	from Ft.Xml.FtMiniDom import NonvalParse
	isrc = InputSourceFactory()

	try:
		try:
			#print "Parsing (with entities)..."
			doc = NonvalParse(isrc.fromString(data, path))
		except:
			print "Parse failed.. retry without entities..."
			data = entrefpattern.sub('&amp;\\1;',data)
			doc = NonvalParse(isrc.fromString(data, path))
	except:
		type, val, tb = sys.exc_info()
		traceback.print_exception(type, val, tb)
		print "parsing failed!"
		print "Data was:"
		print data
		#rox.report_exception()
		raise
	return doc
	
