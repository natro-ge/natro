//! COM objects driven by name, as a script drives them (IDispatch): ADO's, for Windows Search
//! (tools/files.rs), as the Python helper drove them through pywin32. And COM's values
//! (VARIANT), UI Automation's too (tools/uia.rs).

use std::mem::ManuallyDrop;

use windows::Win32::Foundation::{SYSTEMTIME, VARIANT_BOOL};
use windows::Win32::System::Com::{
    CLSCTX_ALL, CLSIDFromProgID, CoCreateInstance, DISPATCH_FLAGS, DISPATCH_METHOD, DISPATCH_PROPERTYGET, DISPPARAMS,
    EXCEPINFO, IDispatch,
};
use windows::Win32::System::Variant::{
    VARENUM, VARIANT, VT_BOOL, VT_BSTR, VT_DATE, VT_DECIMAL, VT_DISPATCH, VT_I4, VT_I8, VT_R8, VT_UI4, VT_UI8,
    VariantClear, VariantTimeToSystemTime,
};
use windows::core::{BSTR, GUID, HSTRING, PCWSTR};

/// The default locale, for IDispatch.
const LOCALE_USER_DEFAULT: u32 = 0x0400;

/// A COM object driven by name, as a script would (ADO's objects).
pub struct Automation(IDispatch);

impl Automation {
    pub fn create(prog_id: PCWSTR) -> Result<Automation, String> {
        unsafe {
            let class = CLSIDFromProgID(prog_id).map_err(|e| e.message())?;
            CoCreateInstance(&class, None, CLSCTX_ALL).map(Automation).map_err(|e| e.message())
        }
    }

    pub fn get(&self, name: &str) -> Result<Owned, String> {
        self.invoke(name, DISPATCH_PROPERTYGET, vec![])
    }

    pub fn call(&self, name: &str, args: Vec<Owned>) -> Result<Owned, String> {
        self.invoke(name, DISPATCH_METHOD | DISPATCH_PROPERTYGET, args)
    }

    fn invoke(&self, name: &str, flags: DISPATCH_FLAGS, mut args: Vec<Owned>) -> Result<Owned, String> {
        unsafe {
            let wide = HSTRING::from(name);
            let names = [PCWSTR(wide.as_ptr())];
            let mut id = 0;
            self.0
                .GetIDsOfNames(&GUID::zeroed(), names.as_ptr(), 1, LOCALE_USER_DEFAULT, &mut id)
                .map_err(|e| format!("{name}: {}", e.message()))?;
            args.reverse(); // IDispatch takes the last first
            let params = DISPPARAMS {
                rgvarg: args.as_mut_ptr().cast(),
                rgdispidNamedArgs: std::ptr::null_mut(),
                cArgs: args.len() as u32,
                cNamedArgs: 0,
            };
            let mut result = Owned(VARIANT::default());
            let mut exception = EXCEPINFO::default();
            let invoked = self.0.Invoke(
                id,
                &GUID::zeroed(),
                LOCALE_USER_DEFAULT,
                flags,
                &params,
                Some(&mut result.0),
                Some(&mut exception),
                None,
            );
            let _ = ManuallyDrop::into_inner(exception.bstrSource);
            let _ = ManuallyDrop::into_inner(exception.bstrHelpFile);
            let said = ManuallyDrop::into_inner(exception.bstrDescription).to_string();
            invoked.map_err(|e| if said.is_empty() { e.message() } else { said.trim().to_string() })?;
            Ok(result)
        }
    }
}

/// A VARIANT that frees what it holds.
#[repr(transparent)]
pub struct Owned(VARIANT);

impl Drop for Owned {
    fn drop(&mut self) {
        unsafe {
            let _ = VariantClear(&mut self.0);
        }
    }
}

pub fn bstr(text: &str) -> Owned {
    let mut value = VARIANT::default();
    unsafe {
        (*value.Anonymous.Anonymous).vt = VT_BSTR;
        (*value.Anonymous.Anonymous).Anonymous.bstrVal = ManuallyDrop::new(BSTR::from(text));
    }
    Owned(value)
}

pub fn int(number: i32) -> Owned {
    let mut value = VARIANT::default();
    unsafe {
        (*value.Anonymous.Anonymous).vt = VT_I4;
        (*value.Anonymous.Anonymous).Anonymous.lVal = number;
    }
    Owned(value)
}

/// Yes or no, as COM writes it.
pub fn yes_no(yes: bool) -> Owned {
    let mut value = VARIANT::default();
    unsafe {
        (*value.Anonymous.Anonymous).vt = VT_BOOL;
        (*value.Anonymous.Anonymous).Anonymous.boolVal = VARIANT_BOOL(if yes { -1 } else { 0 });
    }
    Owned(value)
}

impl From<VARIANT> for Owned {
    fn from(value: VARIANT) -> Owned {
        Owned(value)
    }
}

impl Owned {
    pub fn variant(&self) -> &VARIANT {
        &self.0
    }

    /// Its text, when it's text.
    pub fn text(&self) -> Option<String> {
        (self.kind() == VT_BSTR).then(|| self.string())
    }

    /// Its whole number, when it's a 4-byte one (UI Automation's states).
    pub fn integer(&self) -> Option<i32> {
        (self.kind() == VT_I4).then(|| unsafe { self.0.Anonymous.Anonymous.Anonymous.lVal })
    }

    fn kind(&self) -> VARENUM {
        unsafe { self.0.Anonymous.Anonymous.vt }
    }

    pub fn object(&self) -> Result<Automation, String> {
        let object = if self.kind() == VT_DISPATCH {
            unsafe { (*self.0.Anonymous.Anonymous.Anonymous.pdispVal).clone() }
        } else {
            None
        };
        object.map(Automation).ok_or_else(|| "ADO didn't give back an object".into())
    }

    pub fn boolean(&self) -> bool {
        self.kind() == VT_BOOL && unsafe { self.0.Anonymous.Anonymous.Anonymous.boolVal.0 != 0 }
    }

    pub fn string(&self) -> String {
        if self.kind() == VT_BSTR {
            unsafe { self.0.Anonymous.Anonymous.Anonymous.bstrVal.to_string() }
        } else {
            String::new()
        }
    }

    /// A whole number (ADO gives 8-byte ones as decimals).
    pub fn number(&self) -> Option<u64> {
        let value = unsafe { &self.0.Anonymous.Anonymous.Anonymous };
        unsafe {
            match self.kind() {
                VT_DECIMAL => {
                    let decimal = &self.0.Anonymous.decVal;
                    let (scale, sign) = (decimal.Anonymous1.Anonymous.scale, decimal.Anonymous1.Anonymous.sign);
                    let whole = decimal.Anonymous2.Lo64.checked_div(10u64.checked_pow(scale as u32)?)?;
                    (decimal.Hi32 == 0 && sign == 0).then_some(whole)
                }
                VT_UI8 => Some(value.ullVal),
                VT_I8 => u64::try_from(value.llVal).ok(),
                VT_UI4 => Some(value.ulVal as u64),
                VT_I4 => u64::try_from(value.lVal).ok(),
                VT_R8 if value.dblVal >= 0.0 => Some(value.dblVal as u64),
                _ => None,
            }
        }
    }

    /// A date as "2026-10-01T02:26" ("" when there's none).
    pub fn date(&self) -> String {
        if self.kind() != VT_DATE {
            return String::new();
        }
        let mut time = SYSTEMTIME::default();
        if unsafe { VariantTimeToSystemTime(self.0.Anonymous.Anonymous.Anonymous.date, &mut time) } == 0 {
            return String::new();
        }
        format!("{:04}-{:02}-{:02}T{:02}:{:02}", time.wYear, time.wMonth, time.wDay, time.wHour, time.wMinute)
    }
}
