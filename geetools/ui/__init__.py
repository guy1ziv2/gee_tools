# coding=utf-8
""" User Interface Tools """
from . import chart, imagestrip, ipymap, ipytools, maptool
import ee
import multiprocessing
from .ipymap import Map

if not ee.data._initialized:
    ee.Initialize()


NOTEBOOK = True


def eprint(eeobject, indent=2, notebook=NOTEBOOK, do_async=False):
    """ Print an EE Object. Same as `print(object.getInfo())`

    :param eeobject: object to print
    :type eeobject: ee.ComputedObject
    :param notebook: if True, prints the object as an Accordion Widget for
        the Jupyter Notebook
    :type notebook: bool
    :param indent: indentation of the print output
    :type indent: int
    :param do_async: call getInfo() asynchronously
    :type do_async: bool
    """

    import pprint
    pp = pprint.PrettyPrinter(indent=indent)

    def get_async(eeobject, result):
        obj = ee.deserializer.decode(eeobject)
        try:
            result['result'] = obj.getInfo()
        except:
            raise

    def get_async2(eeobject, result):
        info = eeobject.getInfo()
        result.append(info)

    try:
        if do_async:
            manager = multiprocessing.Manager()
            info = manager.list()
            proxy = ee.serializer.encode(eeobject)
            process = multiprocessing.Process(target=get_async2, args=(eeobject, info))
            process.start()
            # process.join()
        else:
            info = eeobject.getInfo()

    except Exception as e:
        print(str(e))
        info = eeobject

    if not notebook:
        if do_async:
            def finalwait():
                isinfo = len(info) > 0
                while not isinfo:
                    isinfo = len(info) > 0
                pp.pprint(info[0])
            p = multiprocessing.Process(target=finalwait, args=())
            p.start()
        else:
            pp.pprint(info)
    else:
        from geetools.ui.ipytools import create_accordion
        from IPython.display import display
        output = create_accordion(info)
        display(output)