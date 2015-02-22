"""@package AlchemyProvider
Documentation for this module.

More details.
"""

import re
import logging
from log_format import BraceMessage as Lf
from .errors import OverrideConditionTypeError, NoConditionTypeFoundError, NoRunFoundError, OverrideConditionValueError
import sqlalchemy.orm
from sqlalchemy.orm import Session

import datetime

import sqlalchemy
from model import *

import posixpath
import file_archiver
from rcdb.constants import COMPONENT_STAT_KEY
from sqlalchemy.orm.exc import NoResultFound
from sqlalchemy.orm.strategy_options import subqueryload, joinedload

log = logging.getLogger("rcdb.provider")


class RCDBProvider(object):
    """
    RCDB data provider that uses SQLAlchemy for accessing databases
    """

    def __init__(self, connection_string=None):
        self._is_connected = False
        self._are_dirs_loaded = False
        self.path_name_regex = re.compile('^[\w\-_]+$', re.IGNORECASE)
        self._connection_string = ""
        self.logging_enabled = True
        self.engine = None
        self.session = None

        if connection_string:
            self.connect(connection_string)
        """:type : Session """

    # ------------------------------------------------
    # Connects to database using connection string
    # ------------------------------------------------
    def connect(self, connection_string="mysql+mysqlconnector://rcdb@127.0.0.1/rcdb"):
        """
        Connects to database using connection string

        connection string might be in form:
        mysql://<username>:<password>@<mysql.address>:<port> <database>
        sqlite:///path/to/file.sqlite

        :param connection_string: connection string
        :type connection_string: str
        """

        try:
            self.engine = sqlalchemy.create_engine(connection_string)
        except ImportError, err:
            # sql alchemy uses MySQLdb by default. But it might be not install in the system
            # in such case we fallback to mysqlconnector which is embedded in CCDB
            if connection_string.startswith("mysql://") and "No module named MySQLdb" in repr(err):
                connection_string = connection_string.replace("mysql://", "mysql+mysqlconnector://")
                self.engine = sqlalchemy.create_engine(connection_string)
            else:
                raise

        session_type = sessionmaker(bind=self.engine)
        self.session = session_type()
        self._is_connected = True
        self._connection_string = connection_string

    # ------------------------------------------------
    # Closes connection to data
    # ------------------------------------------------
    def disconnect(self):
        """Closes connection to database"""
        self._is_connected = False
        self.session.close()

    # -------------------------------------------------
    # indicates ether the connection is open or not
    # -------------------------------------------------
    @property
    def is_connected(self):
        """
        indicates ether the connection is open or not

        :return: bool True if connection is opened
        :rtype: bool
        """
        return self._is_connected

    # ------------------------------------------------
    # Connection string that was used
    # ------------------------------------------------
    @property
    def connection_string(self):
        """
        Connection string that was used on last connect()

        :return: connection string
        :rtype: str
        """
        return self._connection_string


class ConfigurationProvider(RCDBProvider):
    """
    CCDB data provider that uses SQLAlchemy for accessing databases
    """

    def __init__(self, connection_string=None):
        RCDBProvider.__init__(self, connection_string)

    # ------------------------------------------------
    #
    # ------------------------------------------------
    def obtain_board(self, board_type, serial):
        query = self.session.query(Board).filter(Board.board_type == board_type, Board.serial == serial)
        if not query.count():
            log.debug(Lf("Board type='{}' sn='{}' is not found in DB. Creating record", board_type, serial))
            board = Board()
            board.serial = serial
            board.board_type = board_type
            self.session.add(board)
            self.session.commit()
            log.info(Lf("Board type='{}' sn='{}' added to DB", board_type, serial))
            return board
        else:
            return query.first()

    # ---------------------------
    #
    # ---------------------------
    def obtain_crate(self, name):
        """
        Gets or creates crate with the name
        """
        query = self.session.query(Crate).filter(Crate.name == name)
        if not query.count():
            log.debug(Lf("Crate '{}' is not found in DB. Creating record...", name))
            crate = Crate()
            crate.name = name
            self.session.add(crate)
            self.session.commit()
            log.info(Lf("Crate '{}' is added to DB", name))
            return crate
        else:
            return query.first()

    # -----------------------------------------------------
    #
    # ------------------------------------------------------
    def obtain_board_installation(self, crate, board, slot):
        """
        Gets board installation by crate, board, slot. Create a new one in DB
        if there is no such installation
        """
        # some validation and value checks
        if isinstance(crate, basestring):
            crate = self.obtain_crate(crate)

        if isinstance(board, tuple):
            board_type, serial = board
            board = self.obtain_board(board_type, serial)
        assert isinstance(crate, Crate)
        assert isinstance(board, Board)
        slot = int(slot)

        query = self.session.query(BoardInstallation).filter(BoardInstallation.board_id == board.id,
                                                             BoardInstallation.crate_id == crate.id,
                                                             BoardInstallation.slot == slot)
        if not query.count():
            log.debug(Lf("Board installation for crate='{}', "
                         "board='{}', sn='{}', slot='{}' is not found in DB. Creating record...",
                         crate.name, board.board_type, board.serial, slot))
            installation = BoardInstallation()
            installation.board = board
            installation.crate = crate
            installation.slot = slot
            self.session.add(installation)
            self.session.commit()
            self.add_log_record(installation,
                                "Board installation for crate='{}', board='{}', sn='{}', slot='{}' added to DB".format(
                                    crate.name, board.board_type, board.serial, slot),
                                0)
            return installation
        else:
            return query.first()

    # ------------------------------------------------
    # Gets Run or returns None
    # ------------------------------------------------
    def get_run(self, run_number):
        """Gets Run object from run_number
            :param run_number: the run number
            :param run_number: int

            :return: Run object corresponding to run number or None if there is no such run in DB
            :rtype: Run or None
        """

        query = self.session.query(Run).filter(Run.number == run_number)
        return query.first()

    # ------------------------------------------------
    # Gets or creates RunConfiguration
    # ------------------------------------------------
    def obtain_run(self, run_number):
        """Gets or creates Run with given number
            :type run_number: int
            :rtype: Run
        """
        run = self.get_run(run_number)
        if not run:
            # no run is found
            run = Run()
            run.number = run_number
            self.session.add(run)
            self.session.commit()

        return run

    # ------------------------------------------------
    #
    # ------------------------------------------------
    def obtain_dac_preset(self, board, values):
        """Gets or creates dac preset for board and dac values"""
        query = self.session.query(DacPreset) \
            .filter(DacPreset.board_id == board.id,
                    DacPreset.text_values == list_to_db_text(values))
        if not query.count():
            preset = DacPreset()
            preset.board = board
            preset.values = values
            self.session.add(preset)
            self.session.commit()
        else:
            preset = query.first()

        assert isinstance(preset, DacPreset)
        return preset

    # ------------------------------------------------
    #
    # ------------------------------------------------
    def add_board_config_to_run(self, run, board, dac_preset):
        """sets that the board have the dac preset values in the run"""
        if not isinstance(run, Run):
            run = self.obtain_run(int(run))

        if not isinstance(dac_preset, DacPreset):
            dac_preset = self.obtain_dac_preset(board, dac_preset)

        # query = self.session.query(BoardConfiguration).join(BoardConfiguration.runs) \
        # .filter(RunConfiguration.id == run.id,
        # BoardConfiguration.board_id == board.id,
        #            BoardConfiguration.dac_preset_id == dac_preset.id)

        query = self.session.query(BoardConfiguration) \
            .filter(BoardConfiguration.board_id == board.id,
                    BoardConfiguration.dac_preset_id == dac_preset.id)

        # Get or create board configuration
        if not query.count():
            log.debug(Lf("Board configuration for board.id='{}', dac_preset.id='{}' not found",
                         board.id, dac_preset.id))
            board_config = BoardConfiguration()
            board_config.board = board
            board_config.dac_preset = dac_preset
            self.session.add(board_config)
            self.session.commit()
            self.add_log_record([board_config, board, dac_preset],
                                "Board conf create. board.id='{}', dac_preset.id='{}'".format(board.id, dac_preset.id),
                                run.number)
        else:
            board_config = query.first()

        # check for run!
        if run not in board_config.runs:
            log.debug(Lf("Board configuration id='{}' not found in run='{}'", board_config.id, run.number))
            board_config.runs.append(run)
            self.session.commit()
            self.add_log_record(board_config,
                                "Board conf id='{}' added to run='{}'".format(board_config.id, run.number),
                                run.number)
        else:
            log.debug(Lf("Board configuration id='{}' is already in run='{}'", board_config.id, run.number))

    # ------------------------------------------------
    #
    # ------------------------------------------------
    def add_board_installation_to_run(self, run, board_installation):
        """Adds board installation to run using crate, board, slot
        :run: run number or RunConfiguration object
        :board_installation: board installation object
        """
        if isinstance(board_installation, tuple):
            # it is (crate, board, slot)
            crate, board, slot = board_installation
            board_installation = self.obtain_board_installation(crate, board, slot)

        if not isinstance(run, Run):
            run = self.obtain_run(int(run))
        assert isinstance(board_installation, BoardInstallation)

        if board_installation not in run.board_installations:
            log.debug(Lf("Board installation id='{}' is not associated with run='{}'",
                         board_installation.id, run.number))
            run.board_installations.append(board_installation)
            self.session.commit()
            self.add_log_record(board_installation,
                                "Add board_installation='{}' to run='{}'".format(board_installation.id, run.number),
                                run.number)
        else:
            log.debug(Lf("Board installation id='{}' already associated with run='{}'",
                         board_installation.id, run.number))

    # ------------------------------------------------
    #
    # ------------------------------------------------
    def add_run_statistics(self, run, total_events):
        """adds run statistics like total events number, etc"""
        if not isinstance(run, Run):
            run = self.obtain_run(int(run))

        run.total_events = total_events
        log.debug(Lf("Updating run statistics. total_events='{}'", total_events))

        self.session.commit()
        self.add_log_record(run, "Run statistics updated. total_events='{}'. Etc...".format(total_events), run.number)

    # ------------------------------------------------
    #
    # ------------------------------------------------
    def add_run_start_time(self, run_num, dtm):
        """
            Sets staring time of run
        """
        assert (isinstance(dtm, datetime.datetime))

        log.debug(Lf("Setting start time '{}' to run '{}'", dtm, run_num))
        run = self.obtain_run(run_num)
        run.start_time = dtm
        self.session.commit()
        self.add_log_record(run, "Start time changed to '{}' for run '{}'".format(dtm, run_num), run_num)

    # ------------------------------------------------
    #
    # ------------------------------------------------
    def add_run_end_time(self, run_num, dtm):
        """Adds time of run"""
        assert (isinstance(dtm, datetime.datetime))

        log.debug(Lf("Setting end time '{}' to run '{}'", dtm, run_num))
        run = self.obtain_run(run_num)
        run.end_time = dtm
        self.session.commit()
        log.info(Lf("End time changed to '{}' for run '{}'", dtm, run_num))

    # ------------------------------------------------
    #
    # ------------------------------------------------
    def add_run_component_statistics(self, run_number, actual_time, comp_name, comp_type, evt_rate, data_rate,
                                     evt_number):
        key = COMPONENT_STAT_KEY + comp_name
        value = {"type": comp_type, "event-rate": evt_rate, "data-rate": data_rate, "event-count": evt_number}
        self.add_condition(run_number, key, value, actual_time, "dict")

    # ------------------------------------------------
    #
    # ------------------------------------------------
    def add_configuration_file(self, run_num, path):
        """Adds configuration file to run configuration. If such file exists"""

        log.debug("Processing configuration file")
        check_sum = file_archiver.get_file_sha256(path)
        run_conf = self.obtain_run(run_num)

        # Look, do we have such file?
        file_query = self.session.query(ConfigurationFile) \
            .filter(ConfigurationFile.sha256 == check_sum, ConfigurationFile.path == path)

        if not file_query.count():
            # no such file found!
            log.debug(Lf("|- File '{}' not found in DB", path))

            # create file.
            conf_file = ConfigurationFile()
            conf_file.sha256 = check_sum
            conf_file.path = path
            with open(path) as io_file:
                conf_file.content = io_file.read()

            # put it to DB and associate with run
            self.session.add(conf_file)
            self.session.commit()

            conf_file.runs.append(run_conf)

            # save and exit
            self.session.commit()
            self.add_log_record(conf_file, "File added to DB. Path: '{}'. Run: '{}'".format(path, run_num), run_num)
            return

        # such file already exists! Get it from database
        conf_file = file_query.first()
        log.debug(Lf("|- File '{}' found in DB by id: '{}'", path, conf_file.id))

        # maybe... we even have this file in run conf?
        if conf_file not in run_conf.files:
            conf_file.runs.append(run_conf)
            # run_conf.files.append(conf_file)
            self.session.commit()  # save and exit
            self.add_log_record(conf_file, "File associated. Path: '{}'. Run: '{}'".format(path, run_num), run_num)
        else:
            log.debug(Lf("|- File already associated with run'{}'", run_num))

    # ------------------------------------------------
    #
    # ------------------------------------------------
    def add_log_record(self, table_ids, description, related_run_number):
        """
        Adds log record to the database
        :param table_ids: Str in form tablename_id, or list of such strings, or ModelBase object, or list[ModelBase]
        :type table_ids:list[str] or list[ModelBase] or Base or str

        :param description: Text description of what has been done
        :type description: str

        :param related_run_number:
        :type related_run_number: int
        :return:
        """

        if isinstance(related_run_number, Run):
            related_run_number = related_run_number.number

        record = LogRecord()

        # table ids?
        if isinstance(table_ids, Base):
            record.table_ids = table_ids.log_id
        elif isinstance(table_ids, list):
            if table_ids:
                if isinstance(list[0], ModelBase):
                    record.table_ids = list_to_db_text([item.log_id for item in table_ids])
                elif isinstance(list[0], str):
                    record.table_ids = list_to_db_text(table_ids)
        elif isinstance(table_ids, str):
            record.table_ids = table_ids

        # description
        record.description = str(description)
        record.related_run_number = related_run_number

        # save
        self.session.add(record)
        self.session.commit()
        log.info(description)

    # ------------------------------------------------
    #
    # ------------------------------------------------
    def get_condition_type(self, name):
        """Gets condition type by name

        :param name: name of condition
        :type name: str

        :return: ConditionType corresponding to name
        :rtype: ConditionType
        """
        try:
            return self.session.query(ConditionType).filter(ConditionType.name == name).one()
        except NoResultFound:
            message = "No ConditionType with name='{}' is found in DB".format(name)
            raise NoConditionTypeFoundError(message)


    # ------------------------------------------------
    #
    # ------------------------------------------------
    def create_condition_type(self, name, value_type, is_many_per_run):
        query = self.session.query(ConditionType).filter(ConditionType.name == name)

        if query.count():
            # we've found such type!
            ct = query.first()
            assert isinstance(ct, ConditionType)

            if ct.value_type != value_type or ct.is_many_per_run != is_many_per_run:
                # we've found it, but it differs!
                if ct.value_type != value_type:
                    message = "Condition type with this name exists but is_many_per_run flag differs:" \
                              "Database is_many_per_run={}, new is_many_per_run={}" \
                        .format(ct.is_many_per_run, is_many_per_run)

                if ct.is_many_per_run != is_many_per_run:
                    message = "Condition type with this name exists, but value type differs:" \
                              "Database value_type={}, new value_type={}".format(ct.value_type, value_type)

                raise OverrideConditionTypeError(message)

            # if we are here, selected ct is the same as requested
            return ct
        else:
            # no such ConditionType found in the database
            ct = ConditionType()
            ct.is_many_per_run = is_many_per_run
            ct.name = name
            ct.value_type = value_type
            self.session.add(ct)
            self.session.commit()
            self.add_log_record(ct, "ConditionType created with name='{}', type='{}', is_many_per_run='{}'"
                                .format(name, value_type, is_many_per_run), 0)
            return ct

    # ------------------------------------------------
    #
    # ------------------------------------------------
    def add_condition(self, run_number, key, value, actual_time=None, replace=False):
        """ Adds condition value for the run

        What if such condition value is already exists for this run?
        It depends on 'is_many_per_run'. Another wods if it is possible to have many such conditions per run or not.

        Only one value is allowed for a run (is_many_per_run=False) :
            1. If run has this condition, with the same value and actual_time it does nothing
            2. If value OR actual_time are different then in DB, function check 'replace' flag and do accordingly

        Example:
            db.add_condition_value(1, "event_count", 1000)                  # First addition to DB
            db.add_condition_value(1, "event_count", 1000)                  # Ok. Do nothing, such value already exists
            db.add_condition_value(1, "event_count", 2222)                  # Error. OverrideConditionValueError
            db.add_condition_value(1, "event_count", 2222, replace=True)    # Ok. Replacing existing value
            print(db.get_condition(1, "event_count"))
                value: 2222
                time:  None

            time1 = datetime(2015,9,1,14,21,01, 222)
            time2 = datetime(2015,9,1,14,21,01, 333)
            db.add_condition_value(1, "timed", 1, time1)  # First addition to DB
            db.add_condition_value(1, "timed", 1, time1)  # Ok. Do nothing
            db.add_condition_value(1, "timed", 1, time2)  # Error. Time is different
            db.add_condition_value(1, "timed", 5, time1)  # Error. Value is different
            db.add_condition_value(1, "timed", 5, time2, True)  # Ok. Value replaced

            print(db.get_condition_value(1, "timed"))
                value: 5
                time:  time2

        Many condition values allowed for the run (is_many_per_run=True)
            1. If run has this condition, with the same value and actual_time the func. DOES NOTHING
            2. If run has this conditions but at different time, it adds this condition to DB
            3. If run has this condition at this time

        Example:
            time1 = datetime(2015,9,1,14,21,01, 222)
            time2 = datetime(2015,9,1,14,21,01, 333)
            db.add_condition_value(1, "event_count", 1000)                  # First addition to DB. Time is None
            db.add_condition_value(1, "event_count", 1000)                  # Ok. Do nothing, such value already exists
            db.add_condition_value(1, "event_count", 2222)                  # Error. Another value for time None
            db.add_condition_value(1, "event_count", 2222, replace=True)    # Ok. Replacing existing value for time None
            db.add_condition_value(1, "event_count", 3333, time1)           # Ok. Value for time1 is added to DB
            db.add_condition_value(1, "event_count", 4444, time1)           # Error. Value differs for time1
            db.add_condition_value(1, "event_count", 4444, time2)           # Ok. Add 444 for time2 to DB

            print(db.get_condition_value(1, "event_count"))
              [0: value=2222; time=None
               1: value=3333; time=time1
               2: value=4444; time=time2]


        :param run_number: The run number for this condition value
        :type run_number: int

        :param key: name of condition or ConditionType
        :type condition_type: str or ConditionType

        :param actual_time:
        :type actual_time: datetime.datetime

        :param replace: If true, function replaces existing value
        :type replace: bool

        :return: ConditionValue object from DB
        """

        if actual_time is not None:
            assert (isinstance(actual_time, datetime.datetime))

        run = self.get_run(run_number)
        if not run:
            message = "No run with run_number='{}' found".format(run_number)
            raise NoRunFoundError(message)

        if isinstance(key, ConditionType):
            ct = key
        else:
            assert isinstance(key, str)
            ct = self.get_condition_type(key)

        # Check! maybe ve have such condition value for this run
        condition_value = None
        db_result = self.get_condition(run_number, ct)
        if db_result:


            if ct.is_many_per_run:
                # we have many per run situation
                assert isinstance(db_result, list)

                for db_value in db_result:
                    # we have something...
                    assert db_value.type is ct
                    assert isinstance(db_value, Condition)
                    if ((db_value.time is None) and (actual_time is None)) or (db_value.time == actual_time):
                        # field have the same time. Check value
                        if db_value.value != value:
                            # value mismatch
                            if replace:
                                # We have to replace the old value
                                condition_value = db_value
                                break
                            else:
                                message = "Condition with such time('{}') already exists for the run_number='{}'" \
                                          "but the is different. DB saved value='{}', new value='{}'. " \
                                          "(Add replace=True flag if you want to replace the old value)" \
                                    .format(actual_time, run_number, db_value.value, value)
                                raise OverrideConditionValueError(message)
                        else:
                            # we found the same value. Return it
                            return db_value
            else:
                # one per run situation
                db_value = db_result
                assert isinstance(db_value, Condition)
                if ((db_value.time is None) and actual_time) or \
                        (db_value.time and (actual_time is None)) or \
                        (db_value.time != actual_time):
                    # time mismatch
                    if replace:
                        # We have to replace the old value
                        condition_value = db_value
                    else:
                        message = "Condition with already exists for the run_number='{}'" \
                                  "but the time is different. DB saved time='{}', new time='{}'. " \
                                  "(Add replace=True flag if you want to replace the old value)" \
                            .format(run_number, db_value.time, actual_time)
                        raise OverrideConditionValueError(message)

                if db_value.value != value:
                    # field have different value
                    if replace:
                        # We have to replace the old value
                        condition_value = db_value
                    else:
                        message = "Condition with already exists for the run_number='{}' " \
                                  "but the value is different. DB saved value='{}', new value='{}'. " \
                                  "(Add replace=True flag if you want to replace the old value)" \
                            .format(run_number, db_value.value, value)

                        raise OverrideConditionValueError(message)

        if not condition_value:
            # if we are here, we haven't found the field with the same time and have to add
            condition_value = Condition()
            condition_value.type = ct
            condition_value.run_number = run_number
            self.session.add(condition_value)

        # finally if we are here, we either have to replace or just created the object
        # now we have to only add value and time and save it to DB
        condition_value.value = value
        condition_value.time = actual_time
        self.session.commit()
        return condition_value

    def get_condition(self, run_number, key):
        """ Returns condition value for the run
        If the is_many_per_run flag is allowed per condition, this function returns one of it
        get_condition_list returns multiple values

        :param run_number: the run number
        :type run_number: int

        :param key: Condition name or ConditionType object
        :type key: str or ConditionType

        :return: Value or None if no such ConditionValue attached to the run
        :rtype: Condition
        """
        if isinstance(key, ConditionType):
            ct = key
        else:
            assert isinstance(key, str)
            ct = self.get_condition_type(key)

        query = self.session.query(Condition). \
            filter(Condition.type == ct, Condition.run_number == run_number)

        return query.all() if ct.is_many_per_run else query.first()



        # #try to find such record in DB not to duplicate it
        # query = self.session.query(ConditionType) \
        # .filter(Condition.key == key,
        # Condition.value == value,
        # Condition.actual_time == actual_time,
        #             Condition._run_conf_id == run.id)
        #
        # if query.count():
        #     log.debug(Lf("Run record key='{}', actual_time='{}' is already added to run='{}'",
        #                  key, actual_time, run.number))
        #     return  # such record already in database. Quit
        #
        # #Create a new record
        # record = ConditionType()
        # record.key = key
        # record.value = value
        # record.value_type = value_type
        # record.actual_time = actual_time
        # record.run = run
        #
        # #add record to DB
        # self.session.add(record)
        # self.session.commit()
        # log.info(Lf("Added record of type '{}'", key))

