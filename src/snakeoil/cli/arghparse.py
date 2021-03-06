# Copyright: 2016 Tim Harder <radhermit@gmail.com>
# License: BSD/GPL2

"""Various argparse actions, types, and miscellaneous extensions."""

import argparse
from collections import Counter
import copy
from functools import partial
import os
import sys

from .. import klass
from ..demandload import demandload
from ..mappings import ImmutableDict

demandload(
    'operator:attrgetter',
    'logging',
    'textwrap:dedent',
    'traceback',
    'snakeoil:osutils',
    'snakeoil.obj:popattr',
    'snakeoil.version:get_version',
    'snakeoil.sequences:split_negations,split_elements',
)


# Enable flag to pull extended docs keyword args into arguments during doc
# generation, when disabled the keyword is silently discarded.
_generate_docs = False


@klass.patch('argparse.ArgumentParser.add_subparsers')
@klass.patch('argparse._SubParsersAction.add_parser')
@klass.patch('argparse._ActionsContainer.add_mutually_exclusive_group')
@klass.patch('argparse._ActionsContainer.add_argument_group')
@klass.patch('argparse._ActionsContainer.add_argument')
def _add_argument_docs(orig_func, self, *args, **kwargs):
    """Enable docs keyword argument support for argparse arguments.

    This is used to add extended, rST-formatted docs to man pages (or other
    generated doc formats) without affecting the regular, summarized help
    output for scripts.

    To use, import this module where argparse is used to create parsers so the
    'docs' keyword gets discarded during regular use. For document generation,
    enable the global _generate_docs variable in order to replace the
    summarized help strings with the extended doc strings.
    """
    docs = kwargs.pop('docs', None)
    obj = orig_func(self, *args, **kwargs)
    if _generate_docs and docs is not None:
        if isinstance(docs, (list, tuple)):
            # list args are often used if originator wanted to strip
            # off first description summary line
            docs = '\n'.join(docs)
        docs = '\n'.join(dedent(docs).strip().split('\n'))

        if orig_func.__name__ == 'add_subparsers':
            # store original description before overriding it with extended
            # docs for general subparsers argument groups
            self._subparsers._description = self._subparsers.description
            self._subparsers.description = docs
        elif isinstance(obj, argparse.Action):
            # docs override help for regular arguments
            obj.help = docs
        elif isinstance(obj, argparse._ActionsContainer):
            # store original description before overriding it with extended
            # docs for argument groups
            obj._description = obj.description
            obj.description = docs
    return obj


class ExtendAction(argparse._AppendAction):
    """Force multiple values to always be stored in a flat list."""

    def __call__(self, parser, namespace, values, option_string=None):
        items = copy.copy(argparse._ensure_value(namespace, self.dest, []))
        items.extend(values)
        setattr(namespace, self.dest, items)


class ParseStdin(ExtendAction):
    """Accept arguments from standard input in a blocking fashion."""

    def __init__(self, *args, **kwargs):
        self.allow_stdin = kwargs.pop('allow_stdin', False)
        super().__init__(*args, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        if self.allow_stdin and (values is not None and len(values) == 1 and values[0] == '-'):
            if not sys.stdin.isatty():
                values = [x.strip() for x in sys.stdin.readlines() if x.strip() != '']
                # reassign stdin to allow interactivity (currently only works for unix)
                sys.stdin = open('/dev/tty')
            else:
                raise argparse.ArgumentError(self, "'-' is only valid when piping data in")
        super().__call__(parser, namespace, values, option_string)


class CommaSeparatedValues(argparse._AppendAction):
    """Split comma-separated values into a list."""

    def parse_values(self, values):
        items = []
        if isinstance(values, str):
            items.extend(x for x in values.split(',') if x)
        else:
            for value in values:
                items.extend(x for x in value.split(',') if x)
        return items

    def __call__(self, parser, namespace, values, option_string=None):
        items = self.parse_values(values)
        setattr(namespace, self.dest, items)


class CommaSeparatedValuesAppend(CommaSeparatedValues, ExtendAction):
    """Split comma-separated values and append them to a list.

    Multiple specified options append to instead of override the parsed args list.
    """

    def __call__(self, parser, namespace, values, option_string=None):
        items = self.parse_values(values)
        ExtendAction.__call__(self, parser, namespace, items, option_string)


class CommaSeparatedNegations(argparse._AppendAction):
    """Split comma-separated enabled and disabled values into lists.

    Disabled values are prefixed with "-" while enabled values are entered as
    is.

    For example, from the sequence "-a,b,c,-d" would result in "a" and "d"
    being registered as disabled while "b" and "c" are enabled.
    """

    def parse_values(self, values):
        disabled, enabled = [], []
        if isinstance(values, str):
            values = [values]
        for value in values:
            try:
                neg, pos = split_negations(x for x in value.split(',') if x)
            except ValueError as e:
                raise argparse.ArgumentTypeError(e)
            disabled.extend(neg)
            enabled.extend(pos)
        return disabled, enabled

    def __call__(self, parser, namespace, values, option_string=None):
        disabled, enabled = self.parse_values(values)
        setattr(namespace, self.dest, (disabled, enabled))


class CommaSeparatedNegationsAppend(CommaSeparatedNegations):
    """Split comma-separated enabled and disabled values and append to lists.

    Multiple specified options append to instead of override the parsed args list.
    """

    def __call__(self, parser, namespace, values, option_string=None):
        old = copy.copy(argparse._ensure_value(namespace, self.dest, ([], [])))
        new = self.parse_values(values)
        combined = tuple(o + n for o, n in zip(old, new))
        setattr(namespace, self.dest, combined)


class CommaSeparatedElements(argparse._AppendAction):
    """Split comma-separated disabled/neutral/enabled elements into lists.

    Disabled elements are prefixed with "-", enabled elements are prefixed with
    "+", and neutral elements are unprefixed.

    For example, from the sequence "-a,b,c,-d" would result in "a" and "d"
    being registered as disabled while "b" and "c" are enabled.
    """

    def parse_values(self, values):
        disabled, neutral, enabled = [], [], []
        if isinstance(values, str):
            values = [values]
        for value in values:
            try:
                neg, neu, pos = split_elements(x for x in value.split(',') if x)
            except ValueError as e:
                raise argparse.ArgumentTypeError(e)
            disabled.extend(neg)
            neutral.extend(neu)
            enabled.extend(pos)
        return disabled, neutral, enabled

    def __call__(self, parser, namespace, values, option_string=None):
        disabled, neutral, enabled = self.parse_values(values)
        setattr(namespace, self.dest, (disabled, neutral, enabled))


class CommaSeparatedElementsAppend(CommaSeparatedElements):
    """Split comma-separated enabled and disabled values and append to lists.

    Multiple specified options append to instead of override the parsed args list.
    """

    def __call__(self, parser, namespace, values, option_string=None):
        old = copy.copy(argparse._ensure_value(namespace, self.dest, ([], [], [])))
        new = self.parse_values(values)
        combined = tuple(o + n for o, n in zip(old, new))
        setattr(namespace, self.dest, combined)


class StoreBool(argparse._StoreAction):

    def __init__(self,
                 option_strings,
                 dest,
                 const=None,
                 default=None,
                 required=False,
                 help=None,
                 metavar='BOOLEAN'):
        super().__init__(
            option_strings=option_strings,
            dest=dest,
            const=const,
            default=default,
            type=self.boolean,
            required=required,
            help=help,
            metavar=metavar)

    @staticmethod
    def boolean(value):
        value = value.lower()
        if value in ('y', 'yes', 'true'):
            return True
        elif value in ('n', 'no', 'false'):
            return False
        raise ValueError("value %r must be [y|yes|true|n|no|false]" % (value,))


class EnableDebug(argparse._StoreTrueAction):

    def __call__(self, parser, namespace, values, option_string=None):
        super().__call__(
            parser, namespace, values, option_string=option_string)
        logging.root.setLevel(logging.DEBUG)


class DelayedValue(object):

    def __init__(self, invokable, priority=0):
        self.priority = priority
        if not callable(invokable):
            raise TypeError("invokable must be callable")
        self.invokable = invokable

    def __call__(self, namespace, attr):
        self.invokable(namespace, attr)


class DelayedDefault(DelayedValue):

    @classmethod
    def wipe(cls, attrs, priority):
        if isinstance(attrs, str):
            attrs = (attrs,)
        return cls(partial(cls._wipe, attrs), priority)

    @staticmethod
    def _wipe(attrs, namespace, triggering_attr):
        for attr in attrs:
            try:
                delattr(namespace, attr)
            except AttributeError:
                pass
        try:
            delattr(namespace, triggering_attr)
        except AttributeError:
            pass


class DelayedParse(DelayedValue):

    def __call__(self, namespace, attr):
        self.invokable()


class OrderedParse(DelayedValue):

    def __call__(self, namespace, attr):
        self.invokable(namespace)
        delattr(namespace, attr)


class Delayed(argparse.Action):

    def __init__(self, option_strings, dest, target=None, priority=0, **kwds):
        if target is None:
            raise ValueError("target must be non None for Delayed")

        self.priority = int(priority)
        self.target = target(option_strings=option_strings, dest=dest, **kwds.copy())
        super().__init__(
            option_strings=option_strings[:],
            dest=dest, nargs=kwds.get("nargs", None), required=kwds.get("required", None),
            help=kwds.get("help", None), metavar=kwds.get("metavar", None))

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, DelayedParse(
            partial(self.target, parser, namespace, values, option_string),
            self.priority))


class Expansion(argparse.Action):

    def __init__(self, option_strings, dest, nargs=None, help=None,
                 required=None, subst=None):
        if subst is None:
            raise TypeError("substitution string must be set")
        # simple aliases with no required arguments shouldn't need to specify nargs
        if nargs is None:
            nargs = 0

        super().__init__(
            option_strings=option_strings,
            dest=dest,
            help=help,
            required=required,
            default=False,
            nargs=nargs)
        self.subst = tuple(subst)

    def __call__(self, parser, namespace, values, option_string=None):
        actions = parser._actions
        action_map = {}
        vals = values
        if isinstance(values, str):
            vals = [vals]
        dvals = {str(idx): val for idx, val in enumerate(vals)}
        dvals['*'] = ' '.join(vals)

        for action in actions:
            action_map.update((option, action) for option in action.option_strings)

        for chunk in self.subst:
            option, args = chunk[0], chunk[1:]
            action = action_map.get(option)
            args = [x % dvals for x in args]
            if not action:
                raise ValueError(
                    "unable to find option %r for %r" %
                    (option, self.option_strings))
            if action.type is not None:
                args = list(map(action.type, args))
            if action.nargs in (1, None):
                args = args[0]
            action(parser, namespace, args, option_string=option_string)
        setattr(namespace, self.dest, True)


class _SubParser(argparse._SubParsersAction):

    def add_parser(self, name, cls=None, **kwds):
        """argparser subparser that links description/help if one is specified"""
        description = kwds.get("description")
        help_txt = kwds.get("help")
        if description is None:
            if help_txt is not None:
                kwds["description"] = help_txt
        elif help_txt is None:
            kwds["help"] = description

        # support using a custom parser class for the subparser
        orig_class = self._parser_class
        if cls is not None:
            self._parser_class = cls
        parser = super().add_parser(name, **kwds)
        self._parser_class = orig_class

        return parser

    def __call__(self, parser, namespace, values, option_string=None):
        """override stdlib argparse to revert subparser namespace changes

        Reverts the broken upstream change made in issue #9351 which causes
        issue #23058. This can be dropped when the problem is fixed upstream.
        """
        parser_name = values[0]
        arg_strings = values[1:]

        # set the parser name if requested
        if self.dest is not argparse.SUPPRESS:
            setattr(namespace, self.dest, parser_name)

        # select the parser
        try:
            parser = self._name_parser_map[parser_name]
        except KeyError:
            tup = parser_name, ', '.join(self._name_parser_map)
            msg = _('unknown parser %r (choices: %s)') % tup
            raise argparse.ArgumentError(self, msg)

        # parse all the remaining options into the namespace
        # store any unrecognized options on the object, so that the top
        # level parser can decide what to do with them
        namespace, arg_strings = parser.parse_known_args(arg_strings, namespace)
        if arg_strings:
            vars(namespace).setdefault(argparse._UNRECOGNIZED_ARGS_ATTR, [])
            getattr(namespace, argparse._UNRECOGNIZED_ARGS_ATTR).extend(arg_strings)


class HelpFormatter(argparse.HelpFormatter):
    """Add custom help formatting for comma-separated list actions."""

    def _format_args(self, action, default_metavar):
        get_metavar = self._metavar_formatter(action, default_metavar)
        if isinstance(action, (CommaSeparatedValues, CommaSeparatedValuesAppend)):
            result = '%s[,%s,...]' % get_metavar(2)
        elif isinstance(action, (CommaSeparatedNegations, CommaSeparatedNegationsAppend)):
            result = '%s[,-%s,...]' % get_metavar(2)
        elif isinstance(action, (CommaSeparatedElements, CommaSeparatedElementsAppend)):
            result = '%s[,-%s,+%s...]' % get_metavar(3)
        else:
            result = super()._format_args(action, default_metavar)
        return result


class SortedHelpFormatter(HelpFormatter):
    """Help formatter that sorts arguments by option strings."""

    def add_arguments(self, actions):
        actions = sorted(actions, key=attrgetter('option_strings'))
        super().add_arguments(actions)


class Namespace(argparse.Namespace):
    """Add support for popping attrs from the namespace."""

    def pop(self, key, default=klass._sentinel):
        """Remove and return an object from the namespace if it exists."""
        try:
            return popattr(self, key)
        except AttributeError:
            if default is not klass._sentinel:
                return default
            raise

    def __getattribute__(self, name):
        val = super().__getattribute__(name)
        # collapse any delayed values accessed before arg parsing occurs
        if isinstance(val, DelayedValue):
            val(self, name)
            val = super().__getattribute__(name)
        return val


class ArgumentParser(argparse.ArgumentParser):
    """Extended, argparse-compatible argument parser."""

    def __init__(self, suppress=False, color=True, debug=True, quiet=True,
                 verbose=True, version=True, add_help=True, sorted_help=False,
                 description=None, docs=None, script=None, prog=None, **kwds):
        self.debug = debug and '--debug' in sys.argv[1:]
        self.verbose = int(verbose)
        if self.verbose:
            argv = Counter(sys.argv[1:])
            if argv['-q'] + argv['--quiet']:
                self.verbose = -1
            else:
                self.verbose = argv['-v'] + argv['--verbose']

        # subparser to use if none is specified on the command line and one is required
        self.__default_subparser = None
        # subparsers action object from calling add_subparsers()
        self.__subparsers = None
        # function to execute for this parser
        self.__main_func = None
        # arg checking function to execute for this parser
        self.__final_check = None

        # Store parent parsers allowing for separating parsing args meant for
        # the root command with args targeted to subcommands. This enables
        # usage such as adding conflicting options to both the root command and
        # subcommands without causing issues in addition to helping support
        # default subparsers.
        self._parents = kwds.get('parents', ())

        # extract the description to use and set docs for doc generation
        description = self._update_desc(description, docs)

        # Consumers can provide the 'script=(__file__, __name__)' parameter in
        # order for version and prog values to be automatically extracted.
        if script is not None:
            try:
                script_path, script_module = script
                if not os.path.exists(script_path):
                    raise TypeError
            except TypeError:
                raise ValueError(
                    "invalid script parameter, should be (__file__, __name__)")

            project = script_module.split('.')[0]
            if prog is None:
                prog = script_module.split('.')[-1]

        if sorted_help:
            formatter = SortedHelpFormatter
        else:
            formatter = HelpFormatter

        super().__init__(
            description=description, formatter_class=formatter,
            prog=prog, add_help=False, **kwds)

        # register our custom actions
        self.register('action', 'parsers', _SubParser)
        self.register('action', 'csv', CommaSeparatedValues)
        self.register('action', 'csv_append', CommaSeparatedValuesAppend)
        self.register('action', 'csv_negations', CommaSeparatedNegations)
        self.register('action', 'csv_negations_append', CommaSeparatedNegationsAppend)
        self.register('action', 'csv_elements', CommaSeparatedElements)
        self.register('action', 'csv_elements_append', CommaSeparatedElementsAppend)

        if not suppress:
            if add_help:
                self.add_argument(
                    '-h', '--help', action='help', default=argparse.SUPPRESS,
                    help='show this help message and exit',
                    docs="""
                        Show this help message and exit. To get more
                        information see the related man page.
                    """)
            if version and script is not None:
                # Note that this option will currently only be available on the
                # base command, not on subcommands.
                self.add_argument(
                    '--version', action='version',
                    version=get_version(project, script_path),
                    help="show this program's version info and exit",
                    docs="""
                        Show this program's version information and exit.

                        When running from within a git repo or a version
                        installed from git the latest commit hash and date will
                        be shown.
                    """)
            if debug:
                self.add_argument(
                    '--debug', action=EnableDebug, help='enable debugging checks',
                    docs='Enable debug checks and show verbose debug output.')
            if quiet:
                # TODO: toggle verbose attr to -1 when quiet instead of using a separate attr
                self.add_argument(
                    '-q', '--quiet', action='store_true',
                    help='suppress non-error messages',
                    docs="Suppress non-error, informational messages.")
            if verbose:
                self.add_argument(
                    '-v', '--verbose', action='count',
                    help='show verbose output',
                    docs="Increase the verbosity of various output.")
            if color:
                self.add_argument(
                    '--color', action=StoreBool,
                    default=sys.stdout.isatty(),
                    help='enable/disable color support',
                    docs="""
                        Toggle colored output support. This can be used to forcibly
                        enable color support when piping output or other sitations
                        where stdout is not a tty.
                    """)

    def _update_desc(self, description=None, docs=None):
        """Extract the description to use.

        Assumes first line is the short description and the remainder should be
        used for generated docs.  Generally this works properly if a module's
        __doc__ attr is assigned to the description parameter.
        """
        description_lines = []
        if description is not None:
            description_lines = description.strip().split('\n', 1)
            description = description_lines[0]
        if _generate_docs:
            if docs is None and len(description_lines) == 2:
                docs = description_lines[1]
            self._docs = docs
        self.description = description
        return description

    @klass.cached_property
    def subparsers(self):
        """Return the set of registered subparsers."""
        parsers = {}
        if self._subparsers is not None:
            for x in self._subparsers._actions:
                if isinstance(x, argparse._SubParsersAction):
                    parsers.update(x._name_parser_map)
        return ImmutableDict(parsers)

    def _parse_known_args(self, arg_strings, namespace):
        """Add support for using a specified, default subparser."""
        skip_subparser_fallback = (
            self.__default_subparser is None or  # no default requested
            {'-h', '--help'}.intersection(arg_strings) or  # help requested
            (arg_strings and arg_strings[0] in self.subparsers)  # subparser already determined
        )

        if not skip_subparser_fallback:
            if self.__default_subparser not in self.subparsers:
                raise ValueError(
                    'unknown subparser %r (available subparsers %s)' % (
                    self.__default_subparser, ', '.join(sorted(self.subparsers))))
            # parse all options the parent parsers know about
            for parser in self._parents:
                namespace, arg_strings = parser._parse_known_args(arg_strings, namespace)
            # prepend the default subcommand to the current arg list
            arg_strings = [self.__default_subparser] + arg_strings

        # parse the remaining args
        return super()._parse_known_args(arg_strings, namespace)

    def parse_args(self, args=None, namespace=None):
        if namespace is None:
            namespace = Namespace()

        args, unknown_args = self.parse_known_args(args, namespace)

        # make sure the correct function and prog are set if running a subcommand
        subcmd_parser = self.subparsers.get(getattr(args, 'subcommand', None), None)
        if subcmd_parser is not None:
            namespace.prog = subcmd_parser.prog
            # override the function to be run if the subcommand sets one
            if subcmd_parser.__main_func is not None:
                namespace.main_func = subcmd_parser.__main_func
            # override the arg checking function if the subcommand sets one
            if subcmd_parser.__final_check is not None:
                namespace.final_check = subcmd_parser.__final_check

        if unknown_args:
            self.error('unrecognized arguments: %s' % ' '.join(unknown_args))

        # Two runs are required; first, handle any suppression defaults
        # introduced. Subparsers defaults cannot override the parent parser, as
        # such a subparser can't turn off config/domain for example. So we
        # first find all DelayedDefault run them, then rescan for delayeds to
        # run. This allows subparsers to introduce a default named for
        # themselves that suppresses the parent.

        # intentionally no protection of suppression code; this should
        # just work.

        i = ((attr, val) for attr, val in args.__dict__.items()
             if isinstance(val, DelayedDefault))
        for attr, functor in sorted(i, key=lambda val: val[1].priority):
            functor(args, attr)

        # now run the delays
        i = ((attr, val) for attr, val in args.__dict__.items()
             if isinstance(val, DelayedValue))
        try:
            for attr, delayed in sorted(i, key=lambda val: val[1].priority):
                delayed(args, attr)
        except (TypeError, ValueError) as err:
            raise TypeError("failed loading/parsing '%s': %s" % (attr, str(err)))
        except argparse.ArgumentError:
            err = sys.exc_info()[1]
            self.error(str(err))

        final_check = getattr(args, 'final_check', None)
        if final_check is not None:
            del args.final_check
            final_check(self, args)

        return args

    def error(self, message, status=2):
        """Print an error message and exit.

        Similar to argparse's error() except usage information is not shown by
        default.
        """
        if self.debug:
            traceback.print_exc()
        self.exit(status, '%s: error: %s\n' % (self.prog, message))

    def bind_main_func(self, functor):
        """Decorator to set a main function for the parser."""
        self.set_defaults(main_func=functor)
        self.__main_func = functor
        # override main prog with subcommand prog
        self.set_defaults(prog=self.prog)
        return functor

    def bind_class(self, obj):
        if not isinstance(obj, ArgparseCommand):
            raise ValueError(
                "expected obj to be an instance of "
                "ArgparseCommand; got %r" % (obj,))
        obj.bind_to_parser(self)
        return self

    def bind_delayed_default(self, priority, name=None):
        def f(functor, name=name):
            if name is None:
                name = functor.__name__
            self.set_defaults(**{name: DelayedValue(functor, priority)})
            return functor
        return f

    def bind_parse_priority(self, priority):
        def f(functor):
            name = functor.__name__
            self.set_defaults(**{name: OrderedParse(functor, priority)})
            return functor
        return f

    def add_subparsers(self, default=None, **kwargs):
        # set the default subparser to use
        self.__default_subparser = default

        # If add_subparsers() has already been called return the previous
        # object as argparse doesn't allow multiple objects of this type.
        if self.__subparsers is not None:
            return self.__subparsers

        kwargs.setdefault('title', 'subcommands')
        kwargs.setdefault('dest', 'subcommand')
        kwargs.setdefault('prog', self.prog)
        subparsers = argparse.ArgumentParser.add_subparsers(self, **kwargs)
        subparsers.required = True
        self.__subparsers = subparsers
        return subparsers

    def bind_final_check(self, functor):
        """Decorator to bind a function for argument validation."""
        self.set_defaults(final_check=functor)
        self.__final_check = functor
        return functor


class ArgparseCommand(object):

    def bind_to_parser(self, parser):
        parser.bind_main_func(self)

    def __call__(self, namespace, out, err):
        raise NotImplementedError(self, '__call__')


def existent_path(value):
    """Check if file argument path exists."""
    if not os.path.exists(value):
        raise argparse.ArgumentTypeError("nonexistent path: %r" % (value,))
    try:
        return osutils.abspath(value)
    except EnvironmentError as e:
        raise ValueError("while resolving path %r, encountered error: %r" % (value, e)) from e
