#!/usr/bin/env python
# Copyright (c) 2022 Lincoln D. Stein (https://github.com/lstein)
# Before running stable-diffusion on an internet-isolated machine,
# run this script from one with internet connectivity. The
# two machines must share a common .cache directory.

"""
This is the npyscreen frontend to the model installation application.
The work is actually done in backend code in model_install_backend.py.
"""

import argparse
import curses
import os
import sys
import textwrap
from argparse import Namespace
from multiprocessing import Process
from multiprocessing.connection import Connection, Pipe
from pathlib import Path
from shutil import get_terminal_size
from typing import List

import logging
import npyscreen
import torch
from npyscreen import widget
from omegaconf import OmegaConf

import invokeai.backend.util.logging as logger

from ...backend.install.model_install_backend import (
    Dataset_path,
    default_config_file,
    default_dataset,
    install_requested_models,
    recommended_datasets,
    ModelInstallList,
    UserSelections,
)
from ...backend import ModelManager
from ...backend.util import choose_precision, choose_torch_device
from ...backend.util.logging import InvokeAILogger
from .widgets import (
    CenteredTitleText,
    MultiSelectColumns,
    SingleSelectColumns,
    OffsetButtonPress,
    TextBox,
    BufferBox,
    set_min_terminal_size,
)
from invokeai.app.services.config import get_invokeai_config

# minimum size for the UI
MIN_COLS = 120
MIN_LINES = 52

config = get_invokeai_config(argv=[])

class addModelsForm(npyscreen.FormMultiPage):
    # for responsive resizing - disabled
    # FIX_MINIMUM_SIZE_WHEN_CREATED = False
    
    # for persistence
    current_tab = 0

    def __init__(self, parentApp, name, multipage=False, *args, **keywords):
        self.multipage = multipage
        super().__init__(parentApp=parentApp, name=name, *args, **keywords)

    def create(self):
        self.keypress_timeout = 10
        self.counter = 0
        self.subprocess_connection = None
        
        model_manager = ModelManager(config.model_conf_path)
        
        self.starter_models = OmegaConf.load(Dataset_path)['diffusers']
        self.installed_diffusers_models = self.list_additional_diffusers_models(
             model_manager,
             self.starter_models,
        )
        self.installed_cn_models = model_manager.list_controlnet_models()
        self.installed_lora_models = model_manager.list_lora_models()
        self.installed_ti_models = model_manager.list_ti_models()

        try:
            self.existing_models = OmegaConf.load(default_config_file())
        except:
            self.existing_models = dict()
            
        self.starter_model_list = list(self.starter_models.keys())
        self.installed_models = dict()

        window_width, window_height = get_terminal_size()

        self.nextrely -= 1
        self.add_widget_intelligent(
            npyscreen.FixedText,
            value="Use ctrl-N and ctrl-P to move to the <N>ext and <P>revious fields,",
            editable=False,
            color="CAUTION",
        )
        self.add_widget_intelligent(
            npyscreen.FixedText,
            value="Use cursor arrows to make a selection, and space to toggle checkboxes.",
            editable=False,
            color="CAUTION",
        )
        self.nextrely += 1
        self.tabs = self.add_widget_intelligent(
            SingleSelectColumns,
            values=[
                'STARTER MODELS',
                'MORE DIFFUSION MODELS',
                'CONTROLNET MODELS',
                'LORA/LYCORIS MODELS',
                'TEXTUAL INVERSION MODELS',
            ],
            value=[self.current_tab],
            columns = 5,
            max_height = 2,
            relx=8,
            scroll_exit = True,
        )
        self.tabs.on_changed = self._toggle_tables
        
        top_of_table = self.nextrely
        self.starter_diffusers_models = self.add_starter_diffusers()
        bottom_of_table = self.nextrely

        self.nextrely = top_of_table
        self.diffusers_models = self.add_diffusers_widgets(
            predefined_models=self.installed_diffusers_models,
            model_type='Diffusers',
            window_width=window_width,
        )
        bottom_of_table = max(bottom_of_table,self.nextrely)

        self.nextrely = top_of_table
        self.controlnet_models = self.add_model_widgets(
            predefined_models=self.installed_cn_models,
            model_type='ControlNet',
            window_width=window_width,
        )
        bottom_of_table = max(bottom_of_table,self.nextrely)

        self.nextrely = top_of_table
        self.lora_models = self.add_model_widgets(
            predefined_models=self.installed_lora_models,
            model_type="LoRA/LyCORIS",
            window_width=window_width,
        )
        bottom_of_table = max(bottom_of_table,self.nextrely)

        self.nextrely = top_of_table
        self.ti_models = self.add_model_widgets(
            predefined_models=self.installed_ti_models,
            model_type="Textual Inversion Embeddings",
            window_width=window_width,
        )
        bottom_of_table = max(bottom_of_table,self.nextrely)
                
        self.nextrely = bottom_of_table+1

        self.monitor = self.add_widget_intelligent(
            BufferBox,
            name='Log Messages',
            editable=False,
            max_height = 15,
        )
        
        self.nextrely += 1
        done_label = "INSTALL/REMOVE"
        back_label = "BACK"
        button_length = len(done_label)
        button_offset = 0
        if self.multipage:
            button_length += len(back_label) + 1
            button_offset += len(back_label) + 1
            self.back_button = self.add_widget_intelligent(
                OffsetButtonPress,
                name=back_label,
                relx=(window_width - button_length) // 2,
                offset=-3,
                rely=-3,
                when_pressed_function=self.on_back,
            )
        self.ok_button = self.add_widget_intelligent(
            OffsetButtonPress,
            name=done_label,
            offset=+3,
            relx=button_offset + 1 + (window_width - button_length) // 2,
            rely=-3,
            when_pressed_function=self.on_execute
        )

        self.cancel = self.add_widget_intelligent(
            npyscreen.ButtonPress,
            name="QUIT",
            rely=-3,
            relx=window_width-20,
            when_pressed_function=self.on_cancel,
        )

        # This restores the selected page on return from an installation
        for i in range(1,self.current_tab+1):
            self.tabs.h_cursor_line_down(1)
        self._toggle_tables([self.current_tab])

    ############# diffusers tab ##########        
    def add_starter_diffusers(self)->dict[str, npyscreen.widget]:
        '''Add widgets responsible for selecting diffusers models'''
        widgets = dict()

        starter_model_labels = self._get_starter_model_labels()
        recommended_models = [
            x
            for x in self.starter_model_list
            if self.starter_models[x].get("recommended", False)
        ]
        self.installed_models = sorted(
            [x for x in list(self.starter_models.keys()) if x in self.existing_models]
        )

        widgets.update(
            label1 = self.add_widget_intelligent(
                CenteredTitleText,
                name="Select from a starter set of Stable Diffusion models from HuggingFace.",
                editable=False,
                labelColor="CAUTION",
            )
        )
        
        self.nextrely -= 1
        # if user has already installed some initial models, then don't patronize them
        # by showing more recommendations
        show_recommended = not self.existing_models
        widgets.update(
            models_selected = self.add_widget_intelligent(
                npyscreen.MultiSelect,
                name="Install Starter Models",
                values=starter_model_labels,
                value=[
                    self.starter_model_list.index(x)
                    for x in self.starter_model_list
                    if (show_recommended and x in recommended_models)\
                    or (x in self.existing_models)
                ],
                max_height=len(starter_model_labels) + 1,
                relx=4,
                scroll_exit=True,
            )
        )

        widgets.update(
            purge_deleted = self.add_widget_intelligent(
                npyscreen.Checkbox,
                name="Purge unchecked diffusers models from disk",
                value=False,
                scroll_exit=True,
                relx=4,
            )
        )
        widgets['purge_deleted'].when_value_edited = lambda: self.sync_purge_buttons(widgets['purge_deleted'])
        
        self.nextrely += 1
        return widgets

    ############# Add a set of model install widgets ########
    def add_model_widgets(self,
                          predefined_models: dict[str,bool],
                          model_type: str,
                          window_width: int=120,
                          install_prompt: str=None,
                          )->dict[str,npyscreen.widget]:
        '''Generic code to create model selection widgets'''
        widgets = dict()
        model_list = sorted(predefined_models.keys())
        if len(model_list) > 0:
            max_width = max([len(x) for x in model_list])
            columns = window_width // (max_width+6)  # 6 characters for "[x] " and padding
            columns = min(len(model_list),columns) or 1
            prompt = install_prompt or f"Select the desired {model_type} models to install. Unchecked models will be purged from disk."

            widgets.update(
                label1 = self.add_widget_intelligent(
                    CenteredTitleText,
                    name=prompt,
                    editable=False,
                    labelColor="CAUTION",
                )
            )

            widgets.update(
                models_selected = self.add_widget_intelligent(
                    MultiSelectColumns,
                    columns=columns,
                    name=f"Install {model_type} Models",
                    values=model_list,
                    value=[
                        model_list.index(x)
                        for x in model_list
                        if predefined_models[x]
                    ],
                    max_height=len(model_list)//columns + 1,
                    relx=4,
                    scroll_exit=True,
                )
            )
        
        self.nextrely += 1
        widgets.update(
            label2 = self.add_widget_intelligent(
                npyscreen.TitleFixedText,
                name="Additional URLs or HuggingFace repo_ids to install (Space separated. Use shift-control-V to paste):",
                relx=4,
                color='CONTROL',
                editable=False,
                scroll_exit=True
            )
        )

        self.nextrely -= 1
        widgets.update(
            download_ids = self.add_widget_intelligent(
                TextBox,
                max_height=4,
                scroll_exit=True,
                editable=True,
                relx=4
            )
        )
        return widgets

    ### Tab for arbitrary diffusers widgets ###
    def add_diffusers_widgets(self,
                              predefined_models: dict[str,bool],
                              model_type: str='Diffusers',
                              window_width: int=120,
                              )->dict[str,npyscreen.widget]:
        '''Similar to add_model_widgets() but adds some additional widgets at the bottom
        to support the autoload directory'''
        widgets = self.add_model_widgets(
            predefined_models,
            'Diffusers',
            window_width,
            install_prompt="Additional diffusers models already installed. Uncheck to purge from disk.",
        )

        self.nextrely += 2
        widgets.update(
            purge_deleted = self.add_widget_intelligent(
                npyscreen.Checkbox,
                name="Purge unchecked diffusers models from disk",
                value=False,
                scroll_exit=True,
                relx=4,
            )
        )
        label = "Directory to scan for models to automatically import (<tab> autocompletes):"
        self.nextrely += 2
        widgets.update(
            autoload_directory = self.add_widget_intelligent(
                npyscreen.TitleFilename,
                name=label,
                select_dir=True,
                must_exist=True,
                use_two_lines=False,
                labelColor="DANGER",
                begin_entry_at=len(label)+1,
                scroll_exit=True,
            )
        )
        widgets.update(
            autoscan_on_startup = self.add_widget_intelligent(
                npyscreen.Checkbox,
                name="Scan and import from this directory each time InvokeAI starts",
                value=False,
                relx=4,
                scroll_exit=True,
            )
        )
        widgets['purge_deleted'].when_value_edited = lambda: self.sync_purge_buttons(widgets['purge_deleted'])
        return widgets

    def sync_purge_buttons(self,checkbox):
        value = checkbox.value
        self.starter_diffusers_models['purge_deleted'].value = value
        self.diffusers_models['purge_deleted'].value = value
        
    def resize(self):
        super().resize()
        if (s := self.starter_diffusers_models.get("models_selected")):
            s.values = self._get_starter_model_labels()

    def _toggle_tables(self, value=None):
        selected_tab = value[0]
        widgets = [
            self.starter_diffusers_models,
            self.diffusers_models,
            self.controlnet_models,
            self.lora_models,
            self.ti_models,
        ]

        for group in widgets:
            for k,v in group.items():
                v.hidden = True
                v.editable = False
        for k,v in widgets[selected_tab].items():
            v.hidden = False
            if not isinstance(v,(npyscreen.FixedText, npyscreen.TitleFixedText, CenteredTitleText)):
                v.editable = True
        self.__class__.current_tab = selected_tab  # for persistence
        self.display()

    def _get_starter_model_labels(self) -> List[str]:
        window_width, window_height = get_terminal_size()
        label_width = 25
        checkbox_width = 4
        spacing_width = 2
        description_width = window_width - label_width - checkbox_width - spacing_width
        im = self.starter_models
        names = self.starter_model_list
        descriptions = [
            im[x].description[0 : description_width - 3] + "..."
            if len(im[x].description) > description_width
            else im[x].description
            for x in names
        ]
        return [
            f"%-{label_width}s %s" % (names[x], descriptions[x])
            for x in range(0, len(names))
        ]

            
    def _get_columns(self) -> int:
        window_width, window_height = get_terminal_size()
        cols = (
            4
            if window_width > 240
            else 3
            if window_width > 160
            else 2
            if window_width > 80
            else 1
        )
        return min(cols, len(self.installed_models))

    def on_execute(self):
        self.monitor.entry_widget.buffer(['Processing...'],scroll_end=True)
        self.marshall_arguments()
        app = self.parentApp
        self.display()
        
        # for communication with the subprocess
        parent_conn, child_conn = Pipe()
        p = Process(
            target = process_and_execute,
            kwargs=dict(
                opt = app.opt,
                selections = app.user_selections,
                conn_out = child_conn,
            )
        )
        p.start()
        child_conn.close()
        self.subprocess_connection = parent_conn
        # process_and_execute(app.opt, app.user_selections)

    def on_ok(self):
        self.parentApp.setNextForm(None)
        self.editing = False
        self.parentApp.user_cancelled = False
        self.marshall_arguments()

    def on_back(self):
        self.parentApp.switchFormPrevious()
        self.editing = False

    def on_cancel(self):
        self.parentApp.setNextForm(None)
        self.parentApp.user_cancelled = True
        self.editing = False

    def while_waiting(self):
        app = self.parentApp
        monitor_widget = self.monitor.entry_widget
        if c := self.subprocess_connection:
            while c.poll():
                try:
                    data = c.recv_bytes().decode('utf-8')
                    data.strip('\n')
                    if data=='*done*':
                        self.subprocess_connection = None
                        monitor_widget.buffer(['** Action Complete **'])
                        self.display()
                        # rebuild the form, saving log messages
                        saved_messages = monitor_widget.values
                        app.main_form = app.addForm(
                            "MAIN", addModelsForm, name="Install Stable Diffusion Models"
                        )
                        app.switchForm('MAIN')
                        app.main_form.monitor.entry_widget.values = saved_messages
                        app.main_form.monitor.entry_widget.buffer([''],scroll_end=True)
                        break
                    else:
                        monitor_widget.buffer(
                            textwrap.wrap(data,
                                          width=monitor_widget.width,
                                          subsequent_indent='   ',
                                          ),
                            scroll_end=True
                        )
                        self.display()
                except (EOFError,OSError):
                    self.subprocess_connection = None

    def list_additional_diffusers_models(self,
                                         manager: ModelManager,
                                         starters:dict
                                         )->dict[str,bool]:
        '''Return a dict of all the currently installed models that are not on the starter list'''
        model_info = manager.list_models()
        additional_models = {
            x:True for x in model_info \
            if model_info[x]['format']=='diffusers' \
            and x not in starters
        }
        return additional_models
        
    def marshall_arguments(self):
        """
        Assemble arguments and store as attributes of the application:
        .starter_models: dict of model names to install from INITIAL_CONFIGURE.yaml
                         True  => Install
                         False => Remove
        .scan_directory: Path to a directory of models to scan and import
        .autoscan_on_startup:  True if invokeai should scan and import at startup time
        .import_model_paths:   list of URLs, repo_ids and file paths to import
        """
        # we're using a global here rather than storing the result in the parentapp
        # due to some bug in npyscreen that is causing attributes to be lost
        selections = self.parentApp.user_selections

        # Starter models to install/remove
        starter_models = dict(
            map(
                lambda x: (self.starter_model_list[x], True),
                self.starter_diffusers_models['models_selected'].value,
            )
        )
        selections.purge_deleted_models = self.starter_diffusers_models['purge_deleted'].value or \
            self.diffusers_models['purge_deleted'].value
        
        selections.install_models = [x for x in starter_models if x not in self.existing_models]
        selections.remove_models = [x for x in self.starter_model_list if x in self.existing_models and x not in starter_models]

        # "More" models
        selections.import_model_paths = self.diffusers_models['download_ids'].value.split()
        if diffusers_selected := self.diffusers_models.get('models_selected'):
            selections.remove_models.extend([x
                                             for x in diffusers_selected.values
                                             if self.installed_diffusers_models[x]
                                             and diffusers_selected.values.index(x) not in diffusers_selected.value
                                             ]
                                            )
                                        
        # TODO: REFACTOR THIS REPETITIVE CODE
        if cn_models_selected := self.controlnet_models.get('models_selected'):
            selections.install_cn_models = [cn_models_selected.values[x]
                                            for x in cn_models_selected.value
                                            if not self.installed_cn_models[cn_models_selected.values[x]]
                                            ]
            selections.remove_cn_models = [x
                                           for x in cn_models_selected.values
                                           if self.installed_cn_models[x]
                                           and cn_models_selected.values.index(x) not in cn_models_selected.value
                                           ]
        if (additional_cns := self.controlnet_models['download_ids'].value.split()):
            valid_cns = [x for x in additional_cns if '/' in x]
            selections.install_cn_models.extend(valid_cns)

        # same thing, for LoRAs
        if loras_selected := self.lora_models.get('models_selected'):
            selections.install_lora_models = [loras_selected.values[x]
                                              for x in loras_selected.value
                                              if not self.installed_lora_models[loras_selected.values[x]]
                                              ]
            selections.remove_lora_models = [x
                                             for x in loras_selected.values
                                             if self.installed_lora_models[x]
                                             and loras_selected.values.index(x) not in loras_selected.value
                                             ]
        if (additional_loras := self.lora_models['download_ids'].value.split()):
            selections.install_lora_models.extend(additional_loras)

        # same thing, for TIs
        # TODO: refactor
        if tis_selected := self.ti_models.get('models_selected'):
            selections.install_ti_models = [tis_selected.values[x]
                                            for x in tis_selected.value
                                            if not self.installed_ti_models[tis_selected.values[x]]
                                            ]
            selections.remove_ti_models = [x
                                           for x in tis_selected.values
                                           if self.installed_ti_models[x]
                                           and tis_selected.values.index(x) not in tis_selected.value
                                           ]
                
        if (additional_tis := self.ti_models['download_ids'].value.split()):
            selections.install_ti_models.extend(additional_tis)
            
        # load directory and whether to scan on startup
        selections.scan_directory = self.diffusers_models['autoload_directory'].value
        selections.autoscan_on_startup = self.diffusers_models['autoscan_on_startup'].value


class AddModelApplication(npyscreen.NPSAppManaged):
    def __init__(self,opt):
        super().__init__()
        self.opt = opt
        self.user_cancelled = False
        self.user_selections = UserSelections()

    def onStart(self):
        npyscreen.setTheme(npyscreen.Themes.DefaultTheme)
        self.main_form = self.addForm(
            "MAIN", addModelsForm, name="Install Stable Diffusion Models"
        )

class StderrToMessage():
    def __init__(self, connection: Connection):
        self.connection = connection

    def write(self, data:str):
        self.connection.send_bytes(data.encode('utf-8'))

    def flush(self):
        pass
        
# --------------------------------------------------------
def process_and_execute(opt: Namespace,
                        selections: Namespace,
                        conn_out: Connection=None,
                        ):
    # set up so that stderr is sent to conn_out
    if conn_out:
        translator = StderrToMessage(conn_out)
        sys.stderr = translator
        sys.stdout = translator
        InvokeAILogger.getLogger().handlers[0]=logging.StreamHandler(translator)
    
    models_to_install = selections.install_models
    models_to_remove = selections.remove_models
    directory_to_scan = selections.scan_directory
    scan_at_startup = selections.autoscan_on_startup
    potential_models_to_install = selections.import_model_paths
    install_requested_models(
        diffusers = ModelInstallList(models_to_install, models_to_remove),
        controlnet = ModelInstallList(selections.install_cn_models, selections.remove_cn_models),
        lora = ModelInstallList(selections.install_lora_models, selections.remove_lora_models),
        ti = ModelInstallList(selections.install_ti_models, selections.remove_ti_models),
        scan_directory=Path(directory_to_scan) if directory_to_scan else None,
        external_models=potential_models_to_install,
        scan_at_startup=scan_at_startup,
        precision="float32"
        if opt.full_precision
        else choose_precision(torch.device(choose_torch_device())),
        purge_deleted=selections.purge_deleted_models,
        config_file_path=Path(opt.config_file) if opt.config_file else None,
    )

    if conn_out:
        conn_out.send_bytes('*done*'.encode('utf-8'))
        conn_out.close()


# --------------------------------------------------------
def select_and_download_models(opt: Namespace):
    precision = (
        "float32"
        if opt.full_precision
        else choose_precision(torch.device(choose_torch_device()))
    )
    if opt.default_only:
        install_requested_models(
            install_starter_models=default_dataset(),
            precision=precision,
        )
    elif opt.yes_to_all:
        install_requested_models(
            install_starter_models=recommended_datasets(),
            precision=precision,
        )
    else:
        # needed because the torch library is loaded, even though we don't use it
        torch.multiprocessing.set_start_method("spawn")
        
        set_min_terminal_size(MIN_COLS, MIN_LINES)
        installApp = AddModelApplication(opt)
        installApp.run()
        process_and_execute(opt, installApp.user_selections)

# -------------------------------------
def main():
    parser = argparse.ArgumentParser(description="InvokeAI model downloader")
    parser.add_argument(
        "--full-precision",
        dest="full_precision",
        action=argparse.BooleanOptionalAction,
        type=bool,
        default=False,
        help="use 32-bit weights instead of faster 16-bit weights",
    )
    parser.add_argument(
        "--yes",
        "-y",
        dest="yes_to_all",
        action="store_true",
        help='answer "yes" to all prompts',
    )
    parser.add_argument(
        "--default_only",
        action="store_true",
        help="only install the default model",
    )
    parser.add_argument(
        "--config_file",
        "-c",
        dest="config_file",
        type=str,
        default=None,
        help="path to configuration file to create",
    )
    parser.add_argument(
        "--root_dir",
        dest="root",
        type=str,
        default=None,
        help="path to root of install directory",
    )
    opt = parser.parse_args()

    # setting a global here
    if opt.root and Path(opt.root).exists():
        config.root = Path(opt.root)

    if not (config.root_dir / config.conf_path.parent).exists():
        logger.info(
            "Your InvokeAI root directory is not set up. Calling invokeai-configure."
        )
        from invokeai.frontend.install import invokeai_configure

        invokeai_configure()
        sys.exit(0)

    try:
        select_and_download_models(opt)
    except AssertionError as e:
        logger.error(e)
        sys.exit(-1)
    except KeyboardInterrupt:
        curses.nocbreak()
        curses.echo()
        curses.endwin()
        logger.info("Goodbye! Come back soon.")
    except widget.NotEnoughSpaceForWidget as e:
        if str(e).startswith("Height of 1 allocated"):
            logger.error(
                "Insufficient vertical space for the interface. Please make your window taller and try again"
            )
        elif str(e).startswith("addwstr"):
            logger.error(
                "Insufficient horizontal space for the interface. Please make your window wider and try again."
            )


# -------------------------------------
if __name__ == "__main__":
    main()
