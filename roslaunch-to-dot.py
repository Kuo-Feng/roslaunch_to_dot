#!/usr/bin/env python
'''This script takes a ROS launch file as input and generates a dot graph
file based on the tree of nodes and launch files that will be launched
based on the input launch file.

    $ ./roslaunch-to-dot.py --help
    usage: roslaunch-to-dot.py [-h] [--png] launchFile outputFile

    Create a dot graph file from a ROS launch file.

    positional arguments:
      launchFile  path to the desired launch file
      outputFile  the output dot file to save

    optional arguments:
      -h, --help  show this help message and exit
      --png       automatically convert the dot file to a PNG

'''
import re
import traceback
from sys import argv
from copy import deepcopy
from random import randint
from datetime import datetime
from commands import getoutput
from os import system, environ
from argparse import ArgumentParser
from collections import namedtuple
import xml.etree.ElementTree as ET
from os.path import exists, basename, splitext, sep


# Create a named tuple to store attributes pertaining to a node
Node = namedtuple("Node", [
    "launchFile",  # The launch file that contains this node
    "package",  # The name of the ROS package containing this node
    "nodeType",  # The type of ROS node this is
    "name",  # The name of the ROS node
    "isTestNode"])  # True if this is a test node, False otherwise


# Keep track of a global set of launch files that have already been
# visited so that we can protect ourselves from entering into an
# infinite loop of launch files if there happens to be a recursive
# cycle in the graph
VISITED_LAUNCH_FILES = set()


class LaunchFile:
    '''The LaunchFile class encapsulates a single ROS launch file. This
    class is responsible for parsing the launch file XML and keeping track
    of nodes and launch files that are included within the launch file. It
    is also responsible for properly resolving any ROS launch substitution
    arguments that may exist within strings that it uses within the launch
    file.

    In addition to this, this class is capable of producing the text to
    export this launch file (and all of the nodes and other launch files
    it connects to) into a dot graph.

    '''

    # Identifiers used as elements within a launch file
    ArgTag = "arg"
    GroupTag = "group"
    IncludeTag = "include"
    NodeTag = "node"
    TestTag = "test"

    # Identifiers used as element attribute names within a launch file
    DefaultAttribute = "default"
    FileAttribute = "file"
    IfAttribute = "if"
    NameAttribute = "name"
    PkgAttribute = "pkg"
    TestNameAttribute = "test-name"
    TypeAttribute = "type"
    UnlessAttribute = "unless"
    ValueAttribute = "value"

    # Identifiers used as substitution arguments within a launch
    # file, e.g,. $(find package)
    AnonSubstitutionArg = "anon"
    ArgSubstitutionArg = "arg"
    EnvSubstitutionArg = "env"
    FindSubstitutionArg = "find"
    OptEnvSubstitutionArg = "optenv"

    # Colors used within the dot graph
    LaunchFileColor = "#d3d3d3"
    MissingFileColor = "#cc0000"
    NodeColor = "#6495ed"
    TestNodeColor = "#009900"

    def __init__(self, filename):
        '''
        * filename -- the ROS launch file

        '''
        # Determine if this launch file has been parsed before
        hasVisited = (filename in VISITED_LAUNCH_FILES)

        self.__filename = filename
        VISITED_LAUNCH_FILES.add(filename)

        # Check if the filename actually exists
        self.__missing = (not exists(self.__filename))

        # Dictionary of args defined in the launch file mapping arg name to
        # its resolved value
        self.__args = {}

        # Map launch file substitution arguments (e.g, 'find', 'arg') to the
        # function that handle resolving the substitution argument
        self.__substitutionArgFnMap = {
            self.FindSubstitutionArg: self.__onFindSubstitutionArg,
            self.ArgSubstitutionArg: self.__onArgSubstitutionArg,
            self.EnvSubstitutionArg: self.__onEnvSubstitutionArg,
            self.OptEnvSubstitutionArg: self.__onEnvSubstitutionArg,
            self.AnonSubstitutionArg: self.__onAnonSubstitutionArg,
        }

        # List of launch file objects which are included by the launch file
        self.__includes = []

        # Create a list of launch filenames that cycles back to previously
        # visited launch files so that these cycles can be differentiated
        # in the graph
        self.__cycles = []

        # List of nodes associated with this launch file. Each node in the
        # list is a namedtuple with the following properties:
        #     launchFile, package, nodeType, name, isTestNode
        self.__nodes = []

        # Protect against cycles in the launch files
        if not hasVisited:
            #### Only parse the file when it has not been visited before
            if not self.__missing:
                # Only parse if the file exists
                self.__parseLaunchFile(filename)
            else:
                print "WARNING: Could not locate included launch " \
                    "file: %s" % self.__filename

    #### Getter functions

    def getFilename(self):
        '''Get the filename for this launch file.'''
        return self.__filename

    def isMissing(self):
        '''Determine if this launch file is missing or not.'''
        return self.__missing

    def getNodes(self):
        '''Get all the nodes included by this launch file.'''
        return self.__nodes

    def getCycles(self):
        '''Return the list of launch file names that are included by this
        launch file but are cycles back to previously visited launch files.

        '''
        return self.__cycles

    def getCleanName(self):
        '''Get the clean (no periods) name for this launch file.'''
        return splitext(basename(self.__filename))[0].replace(".", "_")

    def getPackageName(self):
        '''Get the name of the package that contains this launch file.'''
        # Isolate the launch directory which should exist in every
        # launch file path
        dirItems = self.__filename.split("%slaunch%s" % (sep, sep))

        # Should have at least 2 items:
        #     path to package, relative path to package launch file
        if len(dirItems) >= 2:
            packageDir = dirItems[0]

            # The final folder in the package directory should be the
            # name of the associated package
            return basename(packageDir)

        raise Exception("Failed to get package name for: %s" % self.__filename)

    def getAllLaunchFiles(self):
        '''Get the entire list of launch files included because of
        this launch file.

        '''
        launchFiles = [self]  # Add ourself

        # Recursively add all of our children
        for launchFile in self.__includes:
            launchFiles.extend(launchFile.getAllLaunchFiles())

        return launchFiles

    def getAllNodes(self):
        '''Get all of the nodes that will be launched because of this launch
        file and any launch files it includes.

        '''
        allNodes = []

        cleanName = self.getCleanName()

        # Add our own nodes
        for node in self.__nodes:
            allNodes.append(node)

        # Recursively add remaining nodes
        for include in self.__includes:
            allNodes.extend(include.getAllNodes())

        return allNodes

    def getNumNodes(self):
        '''Get the number of unique ROS nodes that this launch tree contains.

        '''
        allNodes = self.getAllNodes()

        uniqueNodes = set()
        for node in allNodes:
            uniqueNodes.add((node.package, node.nodeType, node.name))

        return len(uniqueNodes)

    def getNumLaunchFiles(self):
        '''Get the number of unique launch files that this launch
        graph contains.

        '''
        allLaunchFiles = self.getAllLaunchFiles()

        uniqueLaunchFiles = set()
        for launchFile in allLaunchFiles:
            uniqueLaunchFiles.add(launchFile.getFilename())

        return len(uniqueLaunchFiles)

    def getIncludeMap(self):
        '''Return the dictionary mapping launch filenames to the list
        of launch files that they include.

        '''
        includeMap = {}

        # Include a mapping for my own included launch files
        includeMap[self.__filename] = self.__includes

        # Add mappings for each of the subchildren
        for childLaunch in self.__includes:
            childMappings = childLaunch.getIncludeMap()

            # Join the two dictionaries together
            includeMap = dict(includeMap.items() + childMappings.items())

        return includeMap

    def getPackageMap(self):
        '''Get a dictionary mapping names of ROS packages to a tuple
        where the first item in the tuple is the list of launch files included
        in that ROS package, and the second item in the tuple is the list of
        ROS nodes included from that ROS package.

        '''
        # Grab the list of all launch files
        allLaunchFiles = self.getAllLaunchFiles()

        ########################################
        # Create map as follows:
        #     [package name]: [list of launch files from that package]
        packageLaunchFileMap = {}
        for launchFile in allLaunchFiles:
            packageName = launchFile.getPackageName()

            # Grab the list of launch files used from this package
            # (or create an empty one)
            packageLaunchFiles = packageLaunchFileMap.get(packageName, [])
            packageLaunchFiles.append(launchFile)
            packageLaunchFileMap[packageName] = packageLaunchFiles

        ########################################
        # Create map as follows:
        #     [package name]: [list of ROS nodes from that package]
        packageNodeMap = {}
        for node in self.getAllNodes():
            # Grab the list of nodes used from this package
            # (or create an empty one)
            packageNodes = packageNodeMap.get(node.package, [])
            packageNodes.append(node)
            packageNodeMap[node.package] = packageNodes

        ########################################
        # Join the two dictionaries together into one map as follows:
        #     [package name]: (list of launch files, list of nodes)
        packageMap = {}
        uniquePackages = \
            set(packageLaunchFileMap.keys() + packageNodeMap.keys())

        # Combine all of the packages
        for package in uniquePackages:
            # Grab the launch files and nodes associated with this package
            # (if none, then use an empty list)
            packageLaunchFiles = packageLaunchFileMap.get(package, [])
            packageNodes = packageNodeMap.get(package, [])

            # Create a map from package name to tuple where the first item
            # is the list of launch files in this package, and the second
            # item is the list of nodes in this package
            packageMap[package] = (packageLaunchFiles, packageNodes)

        return packageMap

    #### Dot graph functions

    def toDot(self):
        '''Return the dot file content that represents this launch
        file tree.

        '''
        # Grab items for the generated notice
        stamp = str(datetime.now())
        command = ' '.join(argv)

        # Grab the map of all packages, nodes, and include files
        # used by this launch tree
        packageMap = self.getPackageMap()

        # Grab properties of the graph just for fun
        numPackages = len(packageMap)
        numLaunchFiles = self.getNumLaunchFiles()
        numNodes = self.getNumNodes()

        # Name the graph after the original launch file
        cleanName = self.getCleanName()

        dotLines = [
            'digraph %s_launch_graph {' % cleanName,
            # The generated notice has to go inside of the digraph otherwise
            # Ubuntu doesn't recognize the file as a dot file...
            '    /**',
            '      * This dot file was automatically generated on %s' % stamp,
            '      * By the command:',
            '      *    %s' % command,
            '      *',
            '      * This launch graph has the following properties:',
            '      *    - it contains %s ROS packages' % numPackages,
            '      *    - it contains %s ROS launch files' % numLaunchFiles,
            '      *    - it contains %s ROS nodes' % numNodes,
            '     */',
            '    graph [fontsize=35, ranksep=2, nodesep=2];',
            '    node [fontsize=35];',
            '    compound=true;',  # Allow connections between subgraphs
        ]

        #### Create a subgraph for every known package
        self.__clusterNum = 0
        for packageName, packageTuple in packageMap.iteritems():
            subgraphLines = self.__createPackageSubgraph(
                packageName, packageTuple)

            dotLines.extend(subgraphLines)

        #### Create connections between all launch files
        dotLines.extend([
            '',
            '    // Add connections between launch files',
        ])

        # Iterate over all packages contained in the launch tree
        for _, packageTuple in packageMap.iteritems():
            launchFiles, _nodes = packageTuple

            # Iterate over all launch files in this package
            for launchFile in launchFiles:
                cleanParentName = launchFile.getCleanName()

                # Grab the list of cycles for this launch file
                cycles = launchFile.getCycles()

                # Iterate over all launch files included by the
                # current launch file
                for include in launchFile.__includes:
                    includeFilename = include.getFilename()
                    cleanIncludeName = include.getCleanName()

                    # Determine if this include is a cycle to a previously
                    # visited node
                    isCycle = (includeFilename in cycles)

                    # Select a color depending on if this is a standard
                    # connection between launch files, or a cycle to a
                    # previously parsed launch file
                    color = "red" if isCycle else "black"

                    attributeStr = self.__getAttributeStr([
                        'penwidth=3',
                        'color=%s' % color,
                    ])

                    # Add a comment indicating that this is a cycle edge
                    if isCycle:
                        dotLines.append("    // WARNING: This edge is cycle "
                                        "to a previous launch file")

                    dotLines.extend([
                        '    "launch_%s" -> "launch_%s" [%s];' % \
                            (cleanParentName, cleanIncludeName, attributeStr),
                    ])

        #### Create connections between launch files and nodes
        dotLines.extend([
            '',
            '    // Add connections between launch files and nodes',
        ])

        for _, packageTuple in packageMap.iteritems():
            _launchFiles, nodes = packageTuple

            for node in nodes:
                # Grab the cleaned name of the launch file for this node
                cleanLaunchFile = node.launchFile.getCleanName()

                # Set of attributes to apply to this edge
                attributeStr = self.__getAttributeStr([
                    "penwidth=3",
                ])

                dotLines.extend([
                    '    "launch_%s" -> "node_%s" [%s];' % \
                        (cleanLaunchFile, node.name, attributeStr),
                ])

        dotLines.extend([
            '}',  # end of digraph
        ])

        return '\n'.join(dotLines)

    def __createPackageSubgraph(self, packageName, packageTuple):
        '''Create a subgraph for a single ROS package.

        * packageName -- the name of the ROS package
        * packageTuple -- Tuple (list of launch files, list of nodes)

        '''
        dotLines = []

        # Grab items from the package tuple
        launchFiles, nodes = packageTuple

        dotLines.extend([
            '',
            '    // Subgraph for package: %s' % packageName,
            '    subgraph cluster_%s {' % self.__clusterNum,
            '        label="%s";' % packageName,
            '        penwidth=5;  // Thicker borders on clusters',
        ])
        self.__clusterNum += 1  # Added a new subgraph

        ## Add one node per launch file contained within this package
        if len(launchFiles) > 0:
            dotLines.extend([
                '',
                '        // Launch files contained in this package',
            ])
            for launchFile in launchFiles:
                baseFilename = basename(launchFile.getFilename())
                cleanName = launchFile.getCleanName()

                # Select the color based on whether or not the file is missing
                color = self.MissingFileColor if launchFile.isMissing() else \
                        self.LaunchFileColor

                # List of attributes to apply to this node
                attributeStr = self.__getAttributeStr([
                    'label="%s"' % baseFilename,
                    'shape=rectangle',
                    'style=filled',
                    'fillcolor="%s"' % color,
                ])

                # Add a node for each launch file
                dotLines.extend([
                    '        "launch_%s" [%s];' % (cleanName, attributeStr),
                ])
        else:
            dotLines.extend([
                '',
                '        // This package contains no launch files',
                ])

        ## Add one node per node contained within this package
        if len(nodes) > 0:
            dotLines.extend([
                '',
                '        // ROS nodes contained in this package',
            ])
            for node in nodes:
                name = node.name

                # Change the color to indicate that this is a test node
                color = self.TestNodeColor if node.isTestNode else \
                        self.NodeColor

                # List of attributes to apply to this node
                attributeStr = self.__getAttributeStr([
                    'shape=rectangle',
                    'style=filled',
                    'fillcolor="%s"' % color
                ])

                ## Add a node for each node
                dotLines.extend([
                    '        "node_%s" [label="%s" %s];' % \
                        (name, name, attributeStr),
                ])
        else:
            dotLines.extend([
                '',
                '        // This package contains no ROS nodes',
                ])

        dotLines.extend([
            "    }",  # End of package subgraph
        ])

        return dotLines

    ##### Launch file XML parsing functions

    def __parseLaunchFile(self, filename):
        '''Parse a single launch file.

        * filename -- the launch file

        '''
        tree = ET.parse(filename)
        root = tree.getroot()

        # Parse all of the launch elements. The XML is parsed serially, meaning
        # that if an argument is used before it is defined then it will
        # generate and error
        self.__parseLaunchElements(root)

    def __parseLaunchElements(self, root):
        '''Parse all of the launch file elements to find other launch files as
        well as ROS nodes contained within the launch file.

        * root -- the launch file XML element

        '''
        # Now we can load all include tags and nodes because we have all of the
        # potential arguments we need
        for child in root:
            # Handle all types of tags
            if child.tag == self.ArgTag:
                # Parse the argument
                self.__parseArg(child)
            elif child.tag == self.IncludeTag:
                try:
                    launchFile = self.__parseInclude(child)
                except:
                    traceback.print_exc()
                    continue  # Ignore error
                else:
                    if launchFile is not None:
                        self.__includes.append(launchFile)
            elif child.tag == self.GroupTag:
                try:
                    self.__parseGroup(child)
                except:
                    traceback.print_exc()
                    continue  # Ignore error
            elif child.tag == self.NodeTag:
                try:
                    node = self.__parseNode(child)
                except:
                    traceback.print_exc()
                    continue  # Ignore error
                else:
                    # Node is disabled (i.e., if=false, or unless=true)
                    if node is not None:
                        self.__nodes.append(node)
            elif child.tag == self.TestTag:
                try:
                    testNode = self.__parseTestNode(child)
                except:
                    traceback.print_exc()
                    continue  # Ignore error
                else:
                    # Test node is disabled (i.e., if=false, or unless=true)
                    if testNode is not None:
                        self.__nodes.append(testNode)

    def __parseArg(self, arg):
        '''Parse the argument tag from a launch file.

        * arg -- the argument tag

        '''
        name = self.__getAttribute(arg, self.NameAttribute)

        # Grab the default and standard value
        default = arg.attrib.get(self.DefaultAttribute, None)
        value = arg.attrib.get(self.ValueAttribute, default)

        # If value is None that means neither attribute was defined
        if value is None:
            raise Exception(
                "Argument must define either the %s or the %s attribute" %
                (self.DefaultAttribute, self.ValueAttribute))

        # Any of these attributes may have substitution arguments
        # that need to be resolved
        name = self.__resolveText(name)
        value = self.__resolveText(value)

        # Store the argument
        self.__args[name] = value

    def __parseInclude(self, include):
        '''Parse the include tag from a launch file.

        * include -- the include tag

        '''
        # Make sure the include is enabled before continuing
        if not self.__isEnabled(include):
            return None  # Node is disabled

        filename = self.__getAttribute(include, self.FileAttribute)
        if filename is None:
            raise Exception(
                "Include tag missing %s attribute" % self.FileAttribute)

        #### We have another launch file to resolve and parse
        #

        # Resolve the full path to the include file
        resolved = self.__resolveText(filename)

        # Protect against cycles in the launch file graph
        hasVisited = (resolved in VISITED_LAUNCH_FILES)
        if hasVisited:
            print "ERROR: There is a cycle in the launch file " \
                "graph from: '%s' to '%s'" % (self.__filename, resolved)
            self.__cycles.append(resolved)  # Add the filename

        # Create the new launch file and parse it
        return LaunchFile(resolved)

    def __parseNode(self, node):
        '''Parse the node tag from a launch file.

        * node -- the node tag

        '''
        # Make sure the node is enabled before continuing
        if not self.__isEnabled(node):
            return None  # Node is disabled

        # Grab all of the node attributes
        pkg = self.__getAttribute(node, self.PkgAttribute)
        nodeType = self.__getAttribute(node, self.TypeAttribute)
        name = self.__getAttribute(node, self.NameAttribute)

        # Any of these attributes may have substitution arguments
        # that need to be resolved
        pkg = self.__resolveText(pkg)
        nodeType = self.__resolveText(nodeType)
        name = self.__resolveText(name)

        return Node(self, pkg, nodeType, name, False)  # Not a test node

    def __parseTestNode(self, testNode):
        '''Parse the test tag from a launch file.

        * testNode -- the testNode tag

        '''
        # Make sure the test node is enabled before continuing
        if not self.__isEnabled(testNode):
            return None  # Test node is disabled

        # Grab all of the test node attributes
        pkg = self.__getAttribute(testNode, self.PkgAttribute)
        nodeType = self.__getAttribute(testNode, self.TypeAttribute)
        name = self.__getAttribute(testNode, self.TestNameAttribute)

        # Any of these attributes may have substitution arguments
        # that need to be resolved
        pkg = self.__resolveText(pkg)
        nodeType = self.__resolveText(nodeType)
        name = self.__resolveText(name)

        return Node(self, pkg, nodeType, name, True)  # This is a test node

    def __parseGroup(self, group):
        '''Parse the group tag from a launch file.

        * group -- the group tag

        '''
        # Make sure the group is enabled before continuing
        if not self.__isEnabled(group):
            return None  # Node is disabled

        self.__parseLaunchElements(group)

    def __resolveText(self, text):
        '''Resolve all of the ROS launch substitution argument
        contained within  given text, e.g.,

        <launch>
            <arg name="example" default="hello" />
            <include file="$(find my_package)/launch/$(arg example)" />
        </launch>

        The string: "$(find my_package)/launch/$(arg example).launch" would
        resolve to:

            /path/to/ros/my_package/launch/hello.launch

        "$(find my_package)" was substituted with the path to "my_package" and
        "$(arg example)" was substituted with the value of the argument named
        "example".

        * text -- the text to resolve

        '''
        # Include files can contain substitution arguments that need to be
        # resolved, e.g.,:
        #    $(find package)/launch/file.launch
        #    $(find package)/launch/$(arg camera).launch
        pattern = re.compile("\$\(([a-zA-Z_]+) ([a-zA-Z0-9_]+)\)")

        # Continue until all substitution arguments in the filename
        # have been resolved
        results = pattern.search(text)
        while results is not None:
            fullText = results.group()
            subArg, argument = results.groups()

            # Grab the function to handle this specific substitution argument
            substitutionFn = self.__substitutionArgFnMap.get(subArg, None)
            if substitutionFn is None:
                raise Exception(
                    "Include has unknown substitution argument %s" % subArg)

            # Attempt to resolve the substitution argument
            resolved = substitutionFn(argument)

            # Update the text with the value of the
            # resolved substitution argument
            text = text.replace(fullText, resolved)

            # Check for another command
            results = pattern.search(text)

        return text

    ##### ROS launch command handler functions

    def __onAnonSubstitutionArg(self, name):
        '''Handle the ROS launch 'anon' substitution argument which aims to
        substitute a randomly generated number inside of some text.

        * name -- the name to anonymize

        '''
        # Just return the given name with a random integer attached
        return "%s-%s" % (name, randint(0, 999))

    def __onArgSubstitutionArg(self, arg):
        '''Handle the ROS launch 'arg' substitution argument which aims to
        substitute the value of a launch file argument inside of some text.

        * package -- the package to find

        '''
        if arg not in self.__args:
            raise Exception("Could not resolve unknown arg: '%s'" % arg)
        return self.__args[arg]

    def __onEnvSubstitutionArg(self, env):
        '''Handle the ROS launch 'env' or 'optenv' substitution argument which
        aims to substitute the value of an environment variable inside of
        some text.

        * package -- the package to find

        '''
        # Determine if a default value was supplied
        parts = env.split(" ")
        if len(parts) == 1:
            #### No default value was supplied
            if arg not in environ:
                raise Exception(
                    "Could not find environment variable: '%s'" % env)
            return environ[env]
        else:
            #### A default value was supplied
            env, default = parts
            return environ.get(env, defaultValue)

    def __onFindSubstitutionArg(self, package):
        '''Handle the ROS launch 'find' substitution argument which aims to
        find the path to a specific ROS package.

        * package -- the ROS package to find

        '''
        # Create the command to find the given ROS package
        command = "rospack find %s" % package
        output = getoutput(command)

        # Check for a rospack error, and just propagate it
        if "Error:" in output:
            raise Exception(output)

        return output

    ##### Private helper functions

    def __isEnabled(self, element):
        '''Determine if a ROS launch element is enabled based on the
        value of the 'if' or 'unless' attributes.

        The values for the 'if' and 'unless' atrributes can only be:
            - "true" or "1", or
            - "false" or "0"

        Any other values and this function will raise an Exception.

        * element -- the ROS launch element

        '''
        # Handle the 'if' argument
        ifCase = element.attrib.get(self.IfAttribute, None)
        if ifCase is not None:
            ifCase = self.__resolveText(ifCase)
            if ifCase.lower() in ["false", "0"]:
                return False  # Element is disabled
            elif ifCase.lower() not in ["true", "1"]:
                raise Exception("Invalid value in if attribute: %s" % ifCase)

        # Handle the 'unless' argument
        unlessCase = element.attrib.get(self.UnlessAttribute, None)
        if unlessCase is not None:
            unlessCase = self.__resolveText(unlessCase)
            if unlessCase.lower() in ["true", "1"]:
                return False  # Element is disabled
            elif unlessCase.lower() not in ["false", "0"]:
                raise Exception(
                    "Invalid value in unless attribute: %s" % unlessCase)

        return True  # Element is enabled

    def __getAttributeStr(self, attributes):
        '''Create the dot code to set the given list of attributes on
        a graph, or node.

        * attributes -- the list of attributes to apply

        '''
        return ', '.join(attributes)

    def __getAttribute(self, element, attribute, default=None):
        '''Get an attribute from the given ROS launch XML element. If
        the value does not exist, and the default value given is None
        (its default value), then this function will raise an Exception.

        * element -- the ROS launch element
        * attribute -- the name of the attribute to get
        * default -- the default value

        '''
        value = element.attrib.get(attribute, default)
        if value is None:
            raise Exception("Missing the %s attribute" % attribute)
        return value


if __name__ == '__main__':
    ##### Support various command line arguments
    parser = ArgumentParser(
        description='Create a dot graph file from a ROS launch file.')
    parser.add_argument(
        'launchFile', type=str,
        help='path to the desired launch file')
    parser.add_argument(
        'outputFile',
        help='the output dot file to save')
    parser.add_argument(
        "--png", dest="convertToPng", action="store_true", default=False,
        help="automatically convert the dot file to a PNG")

    # Parse the command line options
    args = parser.parse_args()

    # Grab command line arguments
    launchFile = args.launchFile
    dotFilename = args.outputFile

    ##### Validate the input arguments

    # Make sure the launch file exists
    if not exists(launchFile):
        print "ERROR: Can not find launch file: %s" % launchFile
        exit(1)

    # Make sure the file is actually a launch file
    if not launchFile.lower().endswith(".launch"):
        print "ERROR: Must be given a '.launch' file: %s" % launchFile
        exit(2)

    ##### Parse the launch file as XML
    try:
        launchFile = LaunchFile(launchFile)
    except:
        traceback.print_exc()
        print "ERROR: failed to parse launch file: %s" % launchFile
        exit(3)

    ##### Convert the launch file tree to a dot file
    try:
        dot = launchFile.toDot()
    except:
        traceback.print_exc()
        print "ERROR: failed to generate dot file contents..."
        exit(4)
    else:
        ##### Save the dot file
        fd = open(dotFilename, "w")
        fd.write("%s\n" % dot)  # Add newline at end of file
        fd.close()

        ##### Convert the dot file into a PNG
        if args.convertToPng:
            print "Converting dot file into PNG..."

            # Use the same name as the dot file for the png
            pngFilename = dotFilename.replace(".dot", ".png")

            # Simple command to convert the dot graph into a PNG
            pngCommand = "dot -Tpng %s -o %s" % (dotFilename, pngFilename)

            # Execute the command, and handle basic errors
            if system(pngCommand) != 0:
                 print "ERROR: Failed to convert the dot graph to a PNG!"
                 print "Tried to use the following command to do it:"
                 print pngCommand
            else:
                print "PNG saved to: %s" % pngFilename