"""FSM-состояния для многошаговых сценариев."""
from aiogram.fsm.state import State, StatesGroup


class Login(StatesGroup):
    phone = State()
    code = State()
    twofa = State()


class Transfer(StatesGroup):
    input = State()        # ввод "ДЕНЬ КОЛИЧЕСТВО @username"


class Search(StatesGroup):
    day = State()


class GrantAccess(StatesGroup):
    user = State()


class EditSetting(StatesGroup):
    value = State()        # значение редактируемой настройки (ключ в data)


class Upload(StatesGroup):
    background = State()    # ожидание фото-фона
