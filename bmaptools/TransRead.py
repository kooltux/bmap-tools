""" This module allows opening and reading various kind of files irrespectively
on whether the file is compressed or not. If it is compressed, this module will
decompress the contents on-the-fly. """

import os
import stat
import types
import urllib
import errno

# A list of supported compression types
SUPPORTED_COMPRESSION_TYPES = ('bz2', 'gz', 'tar.gz', 'tgz', 'tar.bz2')

def _fake_seek_forward(self, offset, whence = os.SEEK_SET):
    """ Seek to a specified offset. We only support seeking forward and
    only relative to the beginning of the file and to the current
    position. """

    if whence == os.SEEK_SET:
        new_pos = offset
    elif whence == os.SEEK_CUR:
        new_pos = self._pos + offset
    else:
        raise Error("'seek()' method requires the 'whence' argument " \
                    "to be %d or %d, but %d was passed" \
                    % (os.SEEK_SET, os.SEEK_CUR, whence))

    if new_pos < self._pos:
        raise Error("''seek()' method supports only seeking forward, " \
                    "seeking from %d to %d is not allowed" \
                    % (self._pos, new_pos))

    length = new_pos - self._pos
    to_read = length
    while to_read > 0:
        buf = self.read(to_read)
        if not buf:
            break
        to_read -= len(buf)

    if to_read < 0:
        raise Error("seeked too far: %d instead of %d" \
                    % (self._pos, new_pos))

def _fake_tell(self):
    """ Return the current emulated file position """

    return self._pos

def _add_fake_seek(self):
    """ Add a limitef fake 'seek()' and 'tell()' capability to a file-like
    object 'self'. """

    assert hasattr(self, "_pos") == False
    assert hasattr(self, "seek") == False
    assert hasattr(self, "tell") == False

    self._pos = 0
    self.seek = types.MethodType(_fake_seek_forward, self)
    self.tell = types.MethodType(_fake_tell, self)

class Error(Exception):
    """ A class for exceptions generated by this module. We currently support
    only one type of exceptions, and we basically throw human-readable problem
    description in case of errors. """
    pass

class _CompressedFile:
    """ This class implements transparent reading from a compressed file-like
    object and decompressing its contents on-the-fly. """

    def __init__(self, file_obj, decompress_func):
        """ Class constructor. The 'file_ojb' argument is the compressed
        file-like object to read from. The 'decompress_func()' function is a
        function to use for decompression. """

        self._file_obj = file_obj
        self._decompress_func = decompress_func
        self._buffer = ''
        self._buffer_pos = 0
        self._eof = False

    def _read_from_buffer(self, length):
        """ Read from the internal buffer. """

        buffer_len = len(self._buffer)
        if buffer_len - self._buffer_pos > length:
            data = self._buffer[self._buffer_pos:self._buffer_pos + length]
            self._buffer_pos += length
        else:
            data = self._buffer[self._buffer_pos:]
            self._buffer = ''
            self._buffer_pos = 0

        return data

    def read(self, size):
        """ Read the compressed file, uncompress the data on-the-fly, and
        return 'size' bytes of the uncompressed data. """

        assert self._buffer_pos >= 0
        assert self._buffer_pos <= len(self._buffer)

        if self._eof:
            return ''

        # Fetch the data from the buffers first
        data = self._read_from_buffer(size)
        size -= len(data)

        # If the buffers did not contain all the requested data, read them,
        # decompress, and buffer.
        chunk_size = max(size, 128 * 1024)
        while size > 0:
            buf = self._file_obj.read(chunk_size)
            if not buf:
                self._eof = True
                break

            if self._decompress_func:
                buf = self._decompress_func(buf)
                if not buf:
                    continue

            assert len(self._buffer) == 0
            assert self._buffer_pos == 0

            # The decompressor may return more data than we requested. Save the
            # extra data in an internal buffer.
            if len(buf) >= size:
                self._buffer = buf
                data += self._read_from_buffer(size)
            else:
                data += buf

            size -= len(buf)

        if hasattr(self, "_pos"):
            self._pos += len(data)

        return data

    def close(self):
        """ Close the '_CompressedFile' file-like object. """
        pass

class TransRead:
    """ This class implement the transparent reading functionality. Instances
    of this class are file-like objects which you can read and seek only
    forward.
    """

    def _open_compressed_file(self):
        """ Detect file compression type and open it with the corresponding
        compression module, or just plain 'open() if the file is not
        compressed. """

        try:
            if self.name.endswith('.tar.gz') \
               or self.name.endswith('.tar.bz2') \
               or self.name.endswith('.tgz'):
                import tarfile

                tar = tarfile.open(fileobj = self._file_obj, mode = 'r')
                # The tarball is supposed to contain only one single member
                members = tar.getmembers()
                if len(members) > 1:
                    raise Error("tarball '%s' contains more than one file" \
                                % self.name)
                elif len(members) == 0:
                    raise Error("tarball '%s' is empty (no files)" \
                                % self.name)

                self._transfile_obj = tar.extractfile(members[0])
                self.size = members[0].size
            elif self.name.endswith('.gz'):
                import gzip

                self._transfile_obj = gzip.GzipFile(fileobj = self._file_obj,
                                                    mode = 'rb')
            elif self.name.endswith('.bz2'):
                import bz2

                self._transfile_obj = _CompressedFile(self._file_obj,
                                              bz2.BZ2Decompressor().decompress)
                _add_fake_seek(self._transfile_obj)
            else:
                self.is_compressed = False
                self._transfile_obj = self._file_obj
                if not self.is_url:
                    self.size = os.fstat(self._file_obj.fileno()).st_size
                self._file_obj = None
        except IOError as err:
            raise Error("cannot open file '%s': %s" % (self.name, err))

    def close(self):
        """ Close the file-like object. """

        self.__del__()

    def __init__(self, filepath):
        """ Class constructor. The 'filepath' argument is the full path to the
        file to read transparently. """

        self.name = filepath
        self.size = None
        self.is_compressed = True
        self.is_url = False
        self._file_obj = None
        self._transfile_obj = None

        try:
            self._file_obj = open(self.name, "rb")
        except IOError as err:
            if err.errno == errno.ENOENT:
                try:
                    import urllib2

                    proxy_support = urllib2.ProxyHandler({})
                    opener = urllib2.build_opener(proxy_support)
                    urllib2.install_opener(opener)
                    self._file_obj = urllib2.urlopen(filepath)
                except IOError as err:
                    raise Error("cannot open URL '%s': %s" % (filepath, err))

                self.is_url = True
            else:
                raise Error("cannot open file '%s': %s" % (filepath, err))

        self._open_compressed_file()

    def read(self, size):
        """ Read the data from the file or URL and and uncompress it on-the-fly
        if necessary. """

        return self._transfile_obj.read(size)

    def __del__(self):
        """ The class destructor which closes opened files. """

        if self._transfile_obj:
            self._transfile_obj.close()
        if self._file_obj:
            self._file_obj.close()

    def __getattr__(self, name):
        """ Called for all attributes that do not exist in the 'TransRead'
        class. We are pretending to be file-like objects, so we just return the
        attributes of the '_transfile_obj' file-like object. """

        return getattr(self._transfile_obj, name)
