# -*- coding: utf-8 -*-
#
# This file is part of INGInious. See the LICENSE and the COPYRIGHTS files for
# more information about the licensing of this file.

""" A demo plugin that adds a page """

import os
import web
import hashlib
from collections import OrderedDict
from inginious.frontend.webapp.pages.course_admin.utils import INGIniousAdminPage
from inginious.frontend.webapp.pages.utils import INGIniousAuthPage
from inginious.frontend.webapp.accessible_time import AccessibleTime

PATH_TO_PLUGIN = os.path.abspath(os.path.dirname(__file__))

user_status_cache = {}

def check_key(course_key):
    if not course_key:
        return True
    else:
        request_hash = web.ctx.environ.get("HTTP_X_SAFEEXAMBROWSER_REQUESTHASH", "")
        return hashlib.sha256((web.ctx.home + web.ctx.fullpath + course_key).encode('utf-8')).hexdigest() == request_hash


class ExamAdminPage(INGIniousAdminPage):
    """ A simple demo page showing how to add a new page """

    def GET_AUTH(self, courseid):
        """ GET request """
        course, _ = self.get_course_and_check_rights(courseid, allow_all_staff=False)
        return self.display_page(course)

    def POST_AUTH(self, courseid):
        course, _ = self.get_course_and_check_rights(courseid, allow_all_staff=False)
        error = []
        saved = False
        input_data = web.input()
        course_content = self.course_factory.get_course_descriptor_content(courseid)
        if input_data.get("action", "") == "config":
            course_content["exam_password"] = input_data["password"]
            course_content["seb_hash"] = input_data["sebhash"]
            course_content["exam_active"] = True if input_data["active"] == "true" else False
            self.course_factory.update_course_descriptor_content(courseid, course_content)
        elif input_data.get("action", "") == "finalize":
            if input_data["username"] == "*":
                users = self.user_manager.get_course_registered_users(course, False)
            else:
                users = [input_data["username"]]

            for username in users:
                self.database.exam.find_and_modify({"username": username, "courseid": courseid}, {"$set": {"seb_hash": course_content["seb_hash"]}}, upsert=True)
                user_status_cache[(courseid, username)] = True

            saved = True
        elif input_data.get("action", "") == "cancel":
            if input_data["username"] == "*":
                self.database.exam.delete_many({"courseid": courseid})
            else:
                self.database.exam.delete_one({"username": input_data["username"], "courseid": courseid})

            if input_data["username"] == "*":
                users = self.user_manager.get_course_registered_users(course, False)
            else:
                users = [input_data["username"]]

            for username in users:
                user_status_cache[(courseid, username)] = False

            saved = True

        return self.display_page(course, error, saved)

    def display_page(self, course, errors=None, saved=False):
        course_content = self.course_factory.get_course_descriptor_content(course.get_id())
        users = sorted(list(
            self.user_manager.get_users_info(
                self.user_manager.get_course_registered_users(course, False)).items()),
            key=lambda k: k[1][0] if k[1] is not None else "")

        user_data = OrderedDict([(username, {
            "username": username, "realname": user[0] if user is not None else ""}) for
                                 username, user in users])

        for entry in self.database.exam.find({"courseid": course.get_id(), "username": {"$in": list(user_data.keys())}}):
            user_data[entry['username']].update(entry)

        mysebhash = hashlib.sha256((web.ctx.home + web.ctx.fullpath + course_content.get("seb_hash", "")).encode('utf-8')).hexdigest()
        thesebhash = web.ctx.environ.get("HTTP_X_SAFEEXAMBROWSER_REQUESTHASH", "")

        tpl = self.template_helper.get_custom_renderer(PATH_TO_PLUGIN).admin

        return tpl(PATH_TO_PLUGIN, course, course_content.get("exam_active", False),
                   course_content.get("exam_password", ""), course_content.get("seb_hash", ""), mysebhash, thesebhash, user_data, errors, saved)


class ExamPage(INGIniousAuthPage):
    """ A simple demo page showing how to add a new page """

    def GET_AUTH(self, courseid):
        """ GET request """
        course = self.course_factory.get_course(courseid)
        course_content = course.get_descriptor()

        if course_content.get("exam_active", False) and web.ctx.environ.get("HTTP_X_SAFEEXAMBROWSER_REQUESTHASH", ""):
            return self.display_page(course)
        else:
            raise web.seeother("/course/" + courseid)

    def POST_AUTH(self, courseid):
        course = self.course_factory.get_course(courseid)
        course_content = course.get_descriptor()
        error = ""

        if course_content.get("exam_active", False):
            input_data = web.input()
            username = self.user_manager.session_username()
            is_admin = self.user_manager.has_staff_rights_on_course(course)
            if input_data.get("password", "") != course_content.get("exam_password", ""):
                error = "Wrong password!"
            elif not check_key(course_content.get("seb_hash", "")):
                error = "Access denied."
            elif not is_admin and input_data.get("action", "") == "finalize":
                self.database.exam.find_and_modify({"username": username, "courseid": courseid}, {"$set": {"seb_hash": course_content.get("seb_hash", "")}}, upsert=True)
                user_status_cache[(courseid, username)] = True

        return self.display_page(course, error)

    def display_page(self, course, error=""):
        username = self.user_manager.session_username()
        if get_user_status(course.get_id(), username, self.database, self.user_manager) or error:
            tpl = self.template_helper.get_custom_renderer(PATH_TO_PLUGIN, False).seb_quit
            return tpl(PATH_TO_PLUGIN, course, error, web.ctx.environ.get("HTTP_X_SAFEEXAMBROWSER_REQUESTHASH", ""))
        else:
            raise web.seeother("/course/" + course.get_id())


def get_user_status(courseid, username, database, user_manager):
    if (courseid, username) not in user_status_cache:
        if database.exam.find_one({"courseid": courseid, "username": user_manager.session_username()}):
            user_status_cache[(courseid, username)] = True
        else:
            user_status_cache[(courseid, username)] = False

    return user_status_cache[(courseid, username)]


def course_accessibility(course, default_value, course_factory, database, user_manager):
    descriptor = course.get_descriptor()
    if descriptor.get("exam_active", False):
        # Check for SEB
        if not check_key(descriptor.get("seb_hash", "")):
            return AccessibleTime(False)

        # Check for exam finalization
        courseid = course.get_id()
        username = user_manager.session_username()

        if get_user_status(courseid, username, database, user_manager):
            return AccessibleTime(False)

    return default_value


def main_menu(template_helper, database, user_manager, course_factory):
    if web.ctx.environ.get("HTTP_X_SAFEEXAMBROWSER_REQUESTHASH", ""):
        # We are in SEB : automatic registration
        for course in course_factory.get_all_courses().values():
            descriptor = course.get_descriptor()
            if descriptor.get("exam_active") and check_key(descriptor.get("seb_hash", "")):
                if not user_manager.course_is_user_registered(course) and not user_manager.has_staff_rights_on_course(course):
                   user_manager.course_register_user(course, force=True)
                raise web.seeother("/course/" + course.get_id())
    return ""


def javascript_header(database, user_manager, course_factory):
    if web.ctx.environ.get("HTTP_X_SAFEEXAMBROWSER_REQUESTHASH", ""):
        # We are in SEB : check if the current hash corresponds to a finished active exam:
        finished_exams = list(database.exam.find({"username": user_manager.session_username()}))
        for finished_exam in finished_exams:
            if check_key(finished_exam.get("seb_hash", "")):
                finished_exam_course = course_factory.get_course(finished_exam["courseid"])
                if finished_exam_course.get_descriptor().get("exam_active",
                                                             False) and not user_manager.has_staff_rights_on_course(
                        finished_exam_course):
                    raise web.seeother("/exam/" + finished_exam["courseid"])
    return ""

def add_admin_menu(course):
    """ Add a menu for the contest settings in the administration """
    return ('exam', '<i class="fa fa-gavel fa-fw"></i>&nbsp; Exam')


def course_menu(course, template_helper):
    """ Displays link to finalize exam on the course page"""
    course_content = course.get_descriptor()
    if course_content.get("exam_active", False):
        return str(template_helper.get_custom_renderer(PATH_TO_PLUGIN, False).course_menu(course, course_content.get("exam_password", False)))
    else:
        return ""


class FakeCSSPage(object):
    def GET(self):
        if web.ctx.environ.get("HTTP_X_SAFEEXAMBROWSER_REQUESTHASH", ""):
            return "#logoff_button {display:none;} a.mailto {display:none;}"

class SebQuitPage(object):
    def GET(self):
        return "<html><body><p><a href='" + web.ctx.homepath +"/seb-quit'>Click here to exit</a></p></body></html>"

def init(plugin_manager, course_factory, client, config):
    """ Init the plugin """

    plugin_manager.add_page("/admin/([^/]+)/exam", ExamAdminPage)
    plugin_manager.add_hook('course_admin_menu', add_admin_menu)
    plugin_manager.add_hook('course_accessibility', lambda course, default: course_accessibility(course, default,
                                                                                                 course_factory,
                                                                                                 plugin_manager.get_database(),
                                                                                                 plugin_manager.get_user_manager()))
    plugin_manager.add_hook('course_allow_unregister', lambda course, default: False if course.get_descriptor().get("exam_active", False) else default)
    plugin_manager.add_hook('course_menu', course_menu)
    plugin_manager.add_page("/exam/([^/]+)", ExamPage)
    plugin_manager.add_page("/exam-style.css", FakeCSSPage)
    plugin_manager.add_hook('css', lambda: "/exam-style.css")
    plugin_manager.add_page('/seb-quit', SebQuitPage)
    add_hook(plugin_manager, 'javascript_header', lambda : javascript_header(plugin_manager.get_database(),
                                                                                                 plugin_manager.get_user_manager(), course_factory))

    add_hook(plugin_manager, 'main_menu', lambda template_helper: main_menu(template_helper,
                                                                                      plugin_manager.get_database(),
                                                                                      plugin_manager.get_user_manager(),
                                                                                      course_factory))

def add_hook(plugin_manager, name, callback):
    """ With no exception handling """
    hook_list = plugin_manager.hooks.get(name, [])
    hook_list.append(callback)
    plugin_manager.hooks[name] = hook_list
